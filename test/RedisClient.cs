#:package StackExchange.Redis@3.4.35
#:property PublishAot=false

using System.Net.Security;
using System.Security.Cryptography.X509Certificates;
using StackExchange.Redis;

string port = Environment.GetEnvironmentVariable("TEST_REDIS_PORT") ?? throw new InvalidOperationException("Run test/persistence.py");
var options = ConfigurationOptions.Parse($"localhost:{port},ssl=true,abortConnect=true");
options.Password = Environment.GetEnvironmentVariable("TEST_REDIS_PASSWORD");
using var certificate = X509Certificate2.CreateFromPem(Environment.GetEnvironmentVariable("TEST_TLS_CERT"));
options.CertificateValidation += (_, presented, _, errors) =>
    presented?.Equals(certificate) == true && (errors & ~SslPolicyErrors.RemoteCertificateChainErrors) == 0;

using var redis = await ConnectionMultiplexer.ConnectAsync(options);
var db = redis.GetDatabase();
if (args.Contains("restored"))
{
    if (await db.HashGetAsync("sample:hash", "field") != "value" ||
        await db.SortedSetScoreAsync("sample:sorted-set", "member") != 1 ||
        await db.StreamLengthAsync("sample:stream") != 1)
        throw new InvalidOperationException("Redis collections did not survive the update.");
    return;
}

await db.StringSetAsync("dotnet", "persisted");
if (await db.StringGetAsync("dotnet") != "persisted")
    throw new InvalidOperationException("String round trip failed.");

if (!await db.LockTakeAsync("lock", "owner", TimeSpan.FromSeconds(30)) || !await db.LockReleaseAsync("lock", "owner"))
    throw new InvalidOperationException("Distributed lock acquisition or release failed.");

var result = await db.ScriptEvaluateAsync("redis.call('HSET', KEYS[1], 'field', 'value'); return redis.call('ZADD', KEYS[2], 1, 'member')",
    [new RedisKey("sample:hash"), new RedisKey("sample:sorted-set")]);
if ((long)result != 1 || !(await db.StreamAddAsync("sample:stream", "field", "value")).HasValue)
    throw new InvalidOperationException("Redis collection writes failed.");
