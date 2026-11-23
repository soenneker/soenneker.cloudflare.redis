#:package StackExchange.Redis@3.4.35
#:package Soenneker.Flywheel.Redis@11.0.567
#:package Microsoft.AspNetCore.DataProtection.StackExchangeRedis@11.0.0
#:property PublishAot=false

using System.Net.Security;
using System.Security.Cryptography.X509Certificates;
using System.Xml.Linq;
using Microsoft.AspNetCore.DataProtection.StackExchangeRedis;
using Soenneker.Flywheel.Communication.Dtos;
using Soenneker.Flywheel.Communication.Requests;
using Soenneker.Flywheel.Redis;
using StackExchange.Redis;

string port = Environment.GetEnvironmentVariable("TEST_REDIS_PORT") ?? throw new InvalidOperationException("Run test/persistence.py");
var options = ConfigurationOptions.Parse($"localhost:{port},ssl=true,abortConnect=true");
options.Password = Environment.GetEnvironmentVariable("TEST_REDIS_PASSWORD");
using var certificate = X509Certificate2.CreateFromPem(Environment.GetEnvironmentVariable("TEST_TLS_CERT"));
options.CertificateValidation += (_, presented, _, errors) =>
    presented?.Equals(certificate) == true && (errors & ~SslPolicyErrors.RemoteCertificateChainErrors) == 0;

using var redis = await ConnectionMultiplexer.ConnectAsync(options);
var db = redis.GetDatabase();
var keys = new RedisXmlRepository(() => db, "data-protection");
await using var jobs = new RedisJobStore(_ => Task.FromResult(db), "container-test");
if (args.Contains("restored"))
{
    if (!keys.GetAllElements().Any(element => element.Value == "api-key-ring-test"))
        throw new InvalidOperationException("The API data protection record did not survive the update.");
    var claim = await jobs.Claim("engine", TimeSpan.FromMinutes(1), CancellationToken.None);
    if (claim is null || claim.Job.Id != (string?)await db.StringGetAsync("expected-job"))
        throw new InvalidOperationException("The Engine could not claim the saved Flywheel job.");
    return;
}
keys.StoreElement(new XElement("test", "api-key-ring-test"), "test");
var request = new EnqueueRequest("test.v1", "{}", new JobPolicy(), TimeSpan.Zero);
await db.StringSetAsync("expected-job", await jobs.Enqueue(request, CancellationToken.None));
await db.StringSetAsync("dotnet", "persisted");
if (await db.StringGetAsync("dotnet") != "persisted")
    throw new InvalidOperationException("String round trip failed.");

if (!await db.LockTakeAsync("lock", "owner", TimeSpan.FromSeconds(30)) || !await db.LockReleaseAsync("lock", "owner"))
    throw new InvalidOperationException("Distributed lock acquisition or release failed.");

var result = await db.ScriptEvaluateAsync("redis.call('HSET', KEYS[1], 'job', 'queued'); return redis.call('ZADD', KEYS[2], 1, 'job')",
    [new RedisKey("jobs"), new RedisKey("queue")]);
if ((long)result != 1 || !(await db.StreamAddAsync("events", "state", "queued")).HasValue)
    throw new InvalidOperationException("Job queue operations failed.");
