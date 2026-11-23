#:property PublishAot=false

using System.Net;
using System.Net.Sockets;
using System.Net.WebSockets;

var endpoint = new Uri(Environment.GetEnvironmentVariable("REDIS_RELAY_URL")
                       ?? throw new InvalidOperationException("Set REDIS_RELAY_URL to the Worker's wss:// endpoint."));
if (endpoint.Scheme != "wss")
    throw new InvalidOperationException("REDIS_RELAY_URL must use wss://.");

var token = Environment.GetEnvironmentVariable("REDIS_RELAY_TOKEN");
if (string.IsNullOrWhiteSpace(token))
    throw new InvalidOperationException("Set REDIS_RELAY_TOKEN to the Worker's relay token.");

var port = int.Parse(Environment.GetEnvironmentVariable("REDIS_RELAY_PORT") ?? "6380");
using var listener = new TcpListener(IPAddress.Loopback, port);
listener.Start();
Console.WriteLine($"Redis relay listening on {listener.LocalEndpoint}");

while (true)
{
    var client = await listener.AcceptTcpClientAsync();
    _ = ForwardAsync(client, endpoint, token);
}

static async Task ForwardAsync(TcpClient client, Uri endpoint, string token)
{
    using (client)
    using (var socket = new ClientWebSocket())
    using (var cancel = new CancellationTokenSource())
    {
        try
        {
            client.NoDelay = true;
            socket.Options.SetRequestHeader("Authorization", $"Bearer {token}");
            cancel.CancelAfter(TimeSpan.FromSeconds(30));
            await socket.ConnectAsync(endpoint, cancel.Token);
            cancel.CancelAfter(Timeout.InfiniteTimeSpan);

            var stream = client.GetStream();
            var send = SendAsync(stream, socket, cancel.Token);
            var receive = ReceiveAsync(socket, stream, cancel.Token);
            await Task.WhenAny(send, receive);
            await cancel.CancelAsync();
            socket.Abort();
            await Task.WhenAll(send, receive);
        }
        catch (OperationCanceledException) when (cancel.IsCancellationRequested) { }
        catch (Exception error)
        {
            Console.Error.WriteLine($"Redis relay connection ended ({error.GetType().Name}).");
        }
    }
}

static async Task SendAsync(NetworkStream stream, ClientWebSocket socket, CancellationToken cancel)
{
    var buffer = new byte[32 * 1024];
    int length;
    while ((length = await stream.ReadAsync(buffer.AsMemory(), cancel)) > 0)
        await socket.SendAsync(buffer.AsMemory(0, length), WebSocketMessageType.Binary, true, cancel);
}

static async Task ReceiveAsync(ClientWebSocket socket, NetworkStream stream, CancellationToken cancel)
{
    var buffer = new byte[32 * 1024];
    while (true)
    {
        var result = await socket.ReceiveAsync(buffer.AsMemory(), cancel);
        if (result.MessageType == WebSocketMessageType.Close) return;
        if (result.MessageType != WebSocketMessageType.Binary)
            throw new InvalidDataException("Redis relay received a non-binary message.");
        await stream.WriteAsync(buffer.AsMemory(0, result.Count), cancel);
    }
}
