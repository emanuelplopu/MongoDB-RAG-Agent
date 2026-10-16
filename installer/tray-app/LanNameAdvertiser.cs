// LAN name advertiser
// Answers mDNS ("quellex.local") and LLMNR ("quellex") name queries on the local
// network with this machine's IPv4 address, so colleagues can open
// https://quellex.local without a DNS server or hosts-file edits.
//
// Docker Desktop containers cannot receive LAN multicast, so this has to run on
// the Windows host; the tray app is the long-lived host process we already have.
// No third-party DNS library: the two protocols only need A-record answers,
// which is a few hundred lines of plain DNS wire format (RFC 1035 / RFC 6762 /
// RFC 4795).

using System.Net;
using System.Net.NetworkInformation;
using System.Net.Sockets;
using System.Text;

namespace RecallHub.TrayApp;

/// <summary>
/// Multicast DNS (RFC 6762) and LLMNR (RFC 4795) responder for one host name.
/// </summary>
public sealed class LanNameAdvertiser : IDisposable
{
    private const int MdnsPort = 5353;
    private const int LlmnrPort = 5355;
    private static readonly IPAddress MdnsGroup = IPAddress.Parse("224.0.0.251");
    private static readonly IPAddress LlmnrGroup = IPAddress.Parse("224.0.0.252");

    private const uint MdnsTtlSeconds = 120;
    private const uint MdnsLegacyTtlSeconds = 10;   // RFC 6762 6.7: legacy unicast answers get a short TTL
    private const uint LlmnrTtlSeconds = 30;

    // Windows-only ioctl: stop ICMP "port unreachable" from surfacing as
    // ConnectionReset on the next receive of a UDP socket.
    private const int SioUdpConnReset = -1744830452;

    private readonly string _hostname;
    private readonly CancellationTokenSource _cts = new();
    private readonly object _gate = new();
    private readonly Dictionary<string, DateTime> _lastMulticastAnswer = new();

    private Socket? _mdnsSocket;
    private Socket? _llmnrSocket;
    private Task? _mdnsLoop;
    private Task? _llmnrLoop;
    private InterfaceSnapshot _interfaces = InterfaceSnapshot.Empty;
    private bool _disposed;

    /// <summary>Short host name, e.g. "quellex".</summary>
    public string Hostname => _hostname;

    /// <summary>mDNS name, e.g. "quellex.local".</summary>
    public string MdnsName => _hostname + ".local";

    /// <summary>True while both responders are listening.</summary>
    public bool IsRunning { get; private set; }

    /// <summary>Last start-up or socket error, for the tray menu / log.</summary>
    public string? LastError { get; private set; }

    /// <summary>IPv4 addresses currently announced (all usable interfaces).</summary>
    public IReadOnlyList<IPAddress> AdvertisedAddresses => _interfaces.AllAddresses;

    public LanNameAdvertiser(string hostname)
    {
        if (!IsValidHostname(hostname))
            throw new ArgumentException($"'{hostname}' is not a valid single-label host name", nameof(hostname));
        _hostname = hostname.ToLowerInvariant();
    }

    /// <summary>
    /// A single DNS label: letters, digits and hyphens, 1-63 characters, no
    /// leading/trailing hyphen. Dots are not allowed (".local" is implied).
    /// </summary>
    public static bool IsValidHostname(string? name)
    {
        if (string.IsNullOrWhiteSpace(name) || name.Length > 63) return false;
        if (name[0] == '-' || name[^1] == '-') return false;
        foreach (var c in name)
        {
            if (!(char.IsAsciiLetterOrDigit(c) || c == '-')) return false;
        }
        return true;
    }

    /// <summary>Open the sockets, join the multicast groups and announce the name.</summary>
    public void Start()
    {
        ObjectDisposedException.ThrowIf(_disposed, this);
        if (IsRunning) return;

        _interfaces = InterfaceSnapshot.Capture();
        try
        {
            _mdnsSocket = CreateSocket(MdnsPort, MdnsGroup, _interfaces);
            _llmnrSocket = CreateSocket(LlmnrPort, LlmnrGroup, _interfaces);
        }
        catch (Exception ex)
        {
            LastError = $"{ex.GetType().Name}: {ex.Message}";
            TrayLog.Error($"LAN name advertiser could not start for {MdnsName}: {LastError}");
            _mdnsSocket?.Dispose();
            _llmnrSocket?.Dispose();
            _mdnsSocket = null;
            _llmnrSocket = null;
            return;
        }

        var token = _cts.Token;
        _mdnsLoop = Task.Run(() => ReceiveLoopAsync(_mdnsSocket, isMdns: true, token), token);
        _llmnrLoop = Task.Run(() => ReceiveLoopAsync(_llmnrSocket, isMdns: false, token), token);
        NetworkChange.NetworkAddressChanged += OnNetworkAddressChanged;
        IsRunning = true;
        LastError = null;

        TrayLog.Info($"LAN name advertiser: answering {MdnsName} (mDNS) and {_hostname} (LLMNR) with "
            + string.Join(", ", _interfaces.Items.Select(i => $"{i.Address} [{i.Name}{(i.IsLanUplink ? "" : ", not a LAN uplink")}]"))
            + $"; preferred: {string.Join(", ", _interfaces.PreferredAddresses)}");

        // Start() runs on the UI thread; keep the announcement continuations
        // off the WinForms synchronization context.
        _ = Task.Run(() => AnnounceAsync(token), token);
    }

    /// <summary>Send goodbye records and close the sockets.</summary>
    public void Stop()
    {
        if (!IsRunning) return;
        IsRunning = false;
        NetworkChange.NetworkAddressChanged -= OnNetworkAddressChanged;

        try
        {
            SendGoodbye();
        }
        catch (Exception ex)
        {
            TrayLog.Warn($"LAN name advertiser goodbye failed: {ex.Message}");
        }

        _cts.Cancel();
        _mdnsSocket?.Dispose();
        _llmnrSocket?.Dispose();
        _mdnsSocket = null;
        _llmnrSocket = null;
        TrayLog.Info("LAN name advertiser stopped");
    }

    public void Dispose()
    {
        if (_disposed) return;
        Stop();
        _disposed = true;
        _cts.Dispose();
    }

    // ------------------------------------------------------------------
    // Sockets
    // ------------------------------------------------------------------

    private static Socket CreateSocket(int port, IPAddress group, InterfaceSnapshot interfaces)
    {
        var socket = new Socket(AddressFamily.InterNetwork, SocketType.Dgram, ProtocolType.Udp);
        // Windows' own DNS client already owns 5353/5355; share the ports like
        // every other mDNS implementation does.
        socket.ExclusiveAddressUse = false;
        socket.SetSocketOption(SocketOptionLevel.Socket, SocketOptionName.ReuseAddress, true);
        socket.Bind(new IPEndPoint(IPAddress.Any, port));
        socket.SetSocketOption(SocketOptionLevel.IP, SocketOptionName.PacketInformation, true);
        socket.SetSocketOption(SocketOptionLevel.IP, SocketOptionName.MulticastTimeToLive, 255);
        socket.SetSocketOption(SocketOptionLevel.IP, SocketOptionName.MulticastLoopback, true);
        try
        {
            socket.IOControl((IOControlCode)SioUdpConnReset, new byte[] { 0, 0, 0, 0 }, null);
        }
        catch
        {
            // Not fatal: the receive loop also tolerates ConnectionReset.
        }
        JoinGroups(socket, group, interfaces);
        return socket;
    }

    private static void JoinGroups(Socket socket, IPAddress group, InterfaceSnapshot interfaces)
    {
        foreach (var address in interfaces.AllAddresses)
        {
            try
            {
                socket.SetSocketOption(SocketOptionLevel.IP, SocketOptionName.AddMembership, new MulticastOption(group, address));
            }
            catch (SocketException)
            {
                // Already a member, or the interface vanished between enumeration and join.
            }
        }
    }

    private void OnNetworkAddressChanged(object? sender, EventArgs e)
    {
        try
        {
            var snapshot = InterfaceSnapshot.Capture();
            lock (_gate)
            {
                _interfaces = snapshot;
            }
            if (_mdnsSocket != null) JoinGroups(_mdnsSocket, MdnsGroup, snapshot);
            if (_llmnrSocket != null) JoinGroups(_llmnrSocket, LlmnrGroup, snapshot);
            TrayLog.Info("LAN name advertiser: network changed, now announcing "
                + string.Join(", ", snapshot.PreferredAddresses.Select(a => a.ToString())));
            _ = Task.Run(() => AnnounceAsync(_cts.Token), _cts.Token);
        }
        catch (Exception ex)
        {
            TrayLog.Warn($"LAN name advertiser: interface refresh failed: {ex.Message}");
        }
    }

    // ------------------------------------------------------------------
    // Receive / respond
    // ------------------------------------------------------------------

    private async Task ReceiveLoopAsync(Socket socket, bool isMdns, CancellationToken token)
    {
        var buffer = new byte[9000];
        var anyEndpoint = new IPEndPoint(IPAddress.Any, 0);

        while (!token.IsCancellationRequested)
        {
            try
            {
                var result = await socket.ReceiveMessageFromAsync(buffer, SocketFlags.None, anyEndpoint, token);
                if (result.RemoteEndPoint is not IPEndPoint remote) continue;

                var query = DnsWire.TryParseQuery(buffer.AsSpan(0, result.ReceivedBytes));
                if (query == null) continue;

                await HandleQueryAsync(socket, isMdns, query, remote, result.PacketInformation.Interface, token);
            }
            catch (OperationCanceledException)
            {
                break;
            }
            catch (ObjectDisposedException)
            {
                break;
            }
            catch (SocketException ex) when (ex.SocketErrorCode is SocketError.ConnectionReset or SocketError.MessageSize)
            {
                // Stray ICMP or oversized datagram - keep listening.
            }
            catch (Exception ex)
            {
                TrayLog.Warn($"LAN name advertiser ({(isMdns ? "mDNS" : "LLMNR")}) receive error: {ex.Message}");
                try { await Task.Delay(250, token); } catch (OperationCanceledException) { break; }
            }
        }
    }

    private async Task HandleQueryAsync(Socket socket, bool isMdns, DnsQuery query, IPEndPoint remote, int interfaceIndex, CancellationToken token)
    {
        InterfaceSnapshot snapshot;
        lock (_gate) snapshot = _interfaces;

        var iface = snapshot.FindByIndex(interfaceIndex);
        // Only talk to neighbours: a query that did not come from a directly
        // attached subnet is not something a link-local protocol should answer.
        if (iface != null && !iface.IsOnLink(remote.Address)) return;

        // Answer with the address of the interface the query arrived on when
        // that is a real LAN uplink. Windows' own resolver (and anything on a
        // VM/Tailscale/WSL adapter) queries on every interface at once; those
        // must not learn the Tailscale or vEthernet address, so they get the
        // LAN addresses instead.
        var addresses = iface != null && iface.IsLanUplink
            ? new[] { iface.Address }
            : snapshot.PreferredAddresses;
        if (addresses.Count == 0) return;

        foreach (var question in query.Questions)
        {
            if (!MatchesName(question.Name)) continue;
            if (question.Class is not (DnsWire.ClassIn or DnsWire.ClassAny)) continue;

            bool wantsA = question.Type is DnsWire.TypeA or DnsWire.TypeAny;
            bool wantsAaaa = question.Type == DnsWire.TypeAaaa;
            if (!wantsA && !wantsAaaa) continue;

            if (isMdns)
            {
                // Legacy unicast query (RFC 6762 6.7): source port is not 5353.
                bool legacy = remote.Port != MdnsPort;
                bool unicast = legacy || question.UnicastResponseRequested;

                if (!unicast && IsThrottled(question.Name, question.Type, interfaceIndex))
                    continue;

                var response = DnsWire.BuildMdnsResponse(
                    id: legacy ? query.Id : (ushort)0,
                    question: question,
                    includeQuestion: legacy,
                    addresses: wantsA ? addresses : Array.Empty<IPAddress>(),
                    cacheFlush: !legacy,
                    ttl: legacy ? MdnsLegacyTtlSeconds : MdnsTtlSeconds);

                if (unicast)
                {
                    await socket.SendToAsync(response, SocketFlags.None, remote, token);
                }
                else
                {
                    await SendMulticastAsync(socket, response, MdnsGroup, MdnsPort, iface?.Address, token);
                }
            }
            else
            {
                // LLMNR answers are always unicast to the sender. A name we own
                // but a type we do not have still gets an (empty) answer so the
                // client stops waiting (RFC 4795 2.1.1).
                var response = DnsWire.BuildLlmnrResponse(
                    id: query.Id,
                    question: question,
                    addresses: wantsA ? addresses : Array.Empty<IPAddress>(),
                    ttl: LlmnrTtlSeconds);
                await socket.SendToAsync(response, SocketFlags.None, remote, token);
            }
        }
    }

    private bool MatchesName(string name)
    {
        return string.Equals(name, MdnsName, StringComparison.OrdinalIgnoreCase)
            || string.Equals(name, _hostname, StringComparison.OrdinalIgnoreCase);
    }

    /// <summary>
    /// RFC 6762 6: never multicast the same answer more than once per second
    /// on the same interface. (Per interface: a client that queries on Wi-Fi
    /// and Ethernet at the same moment must get both answers.)
    /// </summary>
    private bool IsThrottled(string name, ushort type, int interfaceIndex)
    {
        var key = $"{name.ToLowerInvariant()}|{type}|{interfaceIndex}";
        var now = DateTime.UtcNow;
        lock (_gate)
        {
            if (_lastMulticastAnswer.TryGetValue(key, out var last) && (now - last) < TimeSpan.FromSeconds(1))
                return true;
            _lastMulticastAnswer[key] = now;
            return false;
        }
    }

    private static async Task SendMulticastAsync(Socket socket, byte[] datagram, IPAddress group, int port, IPAddress? viaInterface, CancellationToken token)
    {
        if (viaInterface != null)
        {
            try
            {
                socket.SetSocketOption(SocketOptionLevel.IP, SocketOptionName.MulticastInterface, viaInterface.GetAddressBytes());
            }
            catch (SocketException)
            {
                // Fall back to the routing table's default interface.
            }
        }
        await socket.SendToAsync(datagram, SocketFlags.None, new IPEndPoint(group, port), token);
    }

    // ------------------------------------------------------------------
    // Unsolicited announcements (RFC 6762 8.3) and goodbye (RFC 6762 10.1)
    // ------------------------------------------------------------------

    private async Task AnnounceAsync(CancellationToken token)
    {
        try
        {
            for (int round = 0; round < 2; round++)
            {
                await Task.Delay(round == 0 ? 1000 : 1000, token);
                var socket = _mdnsSocket;
                if (socket == null || !IsRunning) return;

                InterfaceSnapshot snapshot;
                lock (_gate) snapshot = _interfaces;

                foreach (var iface in snapshot.Items.Where(i => i.IsLanUplink))
                {
                    var record = DnsWire.BuildMdnsResponse(
                        id: 0,
                        question: new DnsQuestion(MdnsName, DnsWire.TypeA, DnsWire.ClassIn, false),
                        includeQuestion: false,
                        addresses: new[] { iface.Address },
                        cacheFlush: true,
                        ttl: MdnsTtlSeconds);
                    await SendMulticastAsync(socket, record, MdnsGroup, MdnsPort, iface.Address, token);
                }
            }
        }
        catch (OperationCanceledException)
        {
        }
        catch (Exception ex)
        {
            TrayLog.Warn($"LAN name advertiser announcement failed: {ex.Message}");
        }
    }

    private void SendGoodbye()
    {
        var socket = _mdnsSocket;
        if (socket == null) return;

        InterfaceSnapshot snapshot;
        lock (_gate) snapshot = _interfaces;

        foreach (var iface in snapshot.Items.Where(i => i.IsLanUplink))
        {
            var record = DnsWire.BuildMdnsResponse(
                id: 0,
                question: new DnsQuestion(MdnsName, DnsWire.TypeA, DnsWire.ClassIn, false),
                includeQuestion: false,
                addresses: new[] { iface.Address },
                cacheFlush: true,
                ttl: 0);
            try
            {
                socket.SetSocketOption(SocketOptionLevel.IP, SocketOptionName.MulticastInterface, iface.Address.GetAddressBytes());
                socket.SendTo(record, new IPEndPoint(MdnsGroup, MdnsPort));
            }
            catch (SocketException)
            {
                // The interface may already be gone; nothing to say goodbye on.
            }
        }
    }

    // ------------------------------------------------------------------
    // Interfaces
    // ------------------------------------------------------------------

    /// <summary>One IPv4 address of one network interface.</summary>
    /// <param name="IsLanUplink">
    /// True for interfaces other computers can actually reach us on: a physical
    /// Ethernet/Wi-Fi adapter with a default gateway. Hyper-V/WSL vEthernet
    /// switches, Tailscale/VPN tunnels and VM host adapters are not.
    /// </param>
    private sealed record LanInterface(int Index, string Name, IPAddress Address, int PrefixLength, bool IsLanUplink)
    {
        public bool IsOnLink(IPAddress other)
        {
            if (other.AddressFamily != AddressFamily.InterNetwork) return false;
            if (IPAddress.IsLoopback(other)) return true;
            var a = BitConverter.ToUInt32(Address.GetAddressBytes().Reverse().ToArray(), 0);
            var b = BitConverter.ToUInt32(other.GetAddressBytes().Reverse().ToArray(), 0);
            var mask = PrefixLength == 0 ? 0u : uint.MaxValue << (32 - PrefixLength);
            return (a & mask) == (b & mask);
        }
    }

    private sealed class InterfaceSnapshot
    {
        public static readonly InterfaceSnapshot Empty = new(new List<LanInterface>());

        public IReadOnlyList<LanInterface> Items { get; }
        public IReadOnlyList<IPAddress> AllAddresses { get; }

        /// <summary>
        /// Addresses worth handing out: the LAN uplinks, or every address when
        /// no interface qualifies (e.g. a machine with a static IP and no gateway).
        /// </summary>
        public IReadOnlyList<IPAddress> PreferredAddresses { get; }

        private InterfaceSnapshot(List<LanInterface> items)
        {
            Items = items;
            AllAddresses = items.Select(i => i.Address).Distinct().ToList();
            var uplinks = items.Where(i => i.IsLanUplink).Select(i => i.Address).Distinct().ToList();
            PreferredAddresses = uplinks.Count > 0 ? uplinks : AllAddresses;
        }

        public LanInterface? FindByIndex(int index) => Items.FirstOrDefault(i => i.Index == index);

        private static readonly string[] VirtualAdapterMarkers =
        {
            "vEthernet", "Hyper-V", "WSL", "Tailscale", "VirtualBox", "VMware", "Wintun",
            "TAP-", "OpenVPN", "WireGuard", "ZeroTier", "Npcap", "Loopback", "Bluetooth",
        };

        /// <summary>
        /// Every operational IPv4 interface except loopback, tunnels and
        /// link-local (169.254.x.x) auto-configuration addresses.
        /// </summary>
        public static InterfaceSnapshot Capture()
        {
            var items = new List<LanInterface>();
            foreach (var ni in NetworkInterface.GetAllNetworkInterfaces())
            {
                if (ni.OperationalStatus != OperationalStatus.Up) continue;
                if (ni.NetworkInterfaceType is NetworkInterfaceType.Loopback or NetworkInterfaceType.Tunnel) continue;
                if (!ni.Supports(NetworkInterfaceComponent.IPv4)) continue;

                IPInterfaceProperties props;
                int index;
                try
                {
                    props = ni.GetIPProperties();
                    index = props.GetIPv4Properties().Index;
                }
                catch (NetworkInformationException)
                {
                    continue;
                }

                bool physicalType = ni.NetworkInterfaceType is NetworkInterfaceType.Ethernet
                    or NetworkInterfaceType.Wireless80211
                    or NetworkInterfaceType.GigabitEthernet
                    or NetworkInterfaceType.FastEthernetT
                    or NetworkInterfaceType.FastEthernetFx;
                bool looksVirtual = VirtualAdapterMarkers.Any(m =>
                    ni.Name.Contains(m, StringComparison.OrdinalIgnoreCase)
                    || ni.Description.Contains(m, StringComparison.OrdinalIgnoreCase));
                bool hasGateway = props.GatewayAddresses.Any(g => g.Address.AddressFamily == AddressFamily.InterNetwork);
                bool isLanUplink = physicalType && !looksVirtual && hasGateway;

                foreach (var unicast in props.UnicastAddresses)
                {
                    var address = unicast.Address;
                    if (address.AddressFamily != AddressFamily.InterNetwork) continue;
                    var bytes = address.GetAddressBytes();
                    if (bytes[0] == 169 && bytes[1] == 254) continue;   // APIPA
                    if (IPAddress.IsLoopback(address)) continue;
                    items.Add(new LanInterface(index, ni.Name, address, unicast.PrefixLength, isLanUplink));
                }
            }
            return new InterfaceSnapshot(items);
        }
    }
}

/// <summary>One question from a DNS query.</summary>
public sealed record DnsQuestion(string Name, ushort Type, ushort Class, bool UnicastResponseRequested);

/// <summary>A parsed DNS query (header id plus questions).</summary>
public sealed record DnsQuery(ushort Id, IReadOnlyList<DnsQuestion> Questions);

/// <summary>
/// Minimal DNS wire-format reader/writer: enough for A-record responders.
/// </summary>
public static class DnsWire
{
    public const ushort TypeA = 1;
    public const ushort TypeAaaa = 28;
    public const ushort TypeNsec = 47;
    public const ushort TypeAny = 255;
    public const ushort ClassIn = 1;
    public const ushort ClassAny = 255;

    private const ushort MdnsCacheFlushBit = 0x8000;
    private const ushort MdnsUnicastResponseBit = 0x8000;

    /// <summary>
    /// Parse a standard query. Returns null for responses, non-standard
    /// opcodes and malformed packets.
    /// </summary>
    public static DnsQuery? TryParseQuery(ReadOnlySpan<byte> data)
    {
        if (data.Length < 12) return null;
        ushort id = ReadUInt16(data, 0);
        ushort flags = ReadUInt16(data, 2);
        bool isResponse = (flags & 0x8000) != 0;
        int opcode = (flags >> 11) & 0x0F;
        if (isResponse || opcode != 0) return null;

        int questionCount = ReadUInt16(data, 4);
        if (questionCount == 0 || questionCount > 32) return null;

        var questions = new List<DnsQuestion>(questionCount);
        int offset = 12;
        for (int i = 0; i < questionCount; i++)
        {
            var name = ReadName(data, ref offset);
            if (name == null || offset + 4 > data.Length) return null;
            ushort type = ReadUInt16(data, offset);
            ushort rawClass = ReadUInt16(data, offset + 2);
            offset += 4;
            bool unicast = (rawClass & MdnsUnicastResponseBit) != 0;
            questions.Add(new DnsQuestion(name, type, (ushort)(rawClass & 0x7FFF), unicast));
        }
        return new DnsQuery(id, questions);
    }

    /// <summary>
    /// Build an mDNS response: A records for <paramref name="addresses"/> plus an
    /// NSEC record stating that only A exists (so clients asking for AAAA stop
    /// waiting, RFC 6762 6.1).
    /// </summary>
    public static byte[] BuildMdnsResponse(ushort id, DnsQuestion question, bool includeQuestion, IReadOnlyList<IPAddress> addresses, bool cacheFlush, uint ttl)
    {
        ushort recordClass = cacheFlush ? (ushort)(ClassIn | MdnsCacheFlushBit) : ClassIn;
        var buffer = new List<byte>(128);

        WriteUInt16(buffer, id);
        WriteUInt16(buffer, 0x8400);                       // QR=1, AA=1
        WriteUInt16(buffer, (ushort)(includeQuestion ? 1 : 0));
        WriteUInt16(buffer, (ushort)(addresses.Count + 1)); // A records + NSEC
        WriteUInt16(buffer, 0);
        WriteUInt16(buffer, 0);

        if (includeQuestion)
        {
            WriteName(buffer, question.Name);
            WriteUInt16(buffer, question.Type);
            WriteUInt16(buffer, question.Class);
        }

        foreach (var address in addresses)
        {
            WriteARecord(buffer, question.Name, recordClass, ttl, address);
        }
        WriteNsecRecord(buffer, question.Name, recordClass, ttl);
        return buffer.ToArray();
    }

    /// <summary>Build an LLMNR response (unicast, question echoed, A records only).</summary>
    public static byte[] BuildLlmnrResponse(ushort id, DnsQuestion question, IReadOnlyList<IPAddress> addresses, uint ttl)
    {
        var buffer = new List<byte>(96);
        WriteUInt16(buffer, id);
        WriteUInt16(buffer, 0x8000);                       // QR=1, C=0, TC=0, T=0
        WriteUInt16(buffer, 1);
        WriteUInt16(buffer, (ushort)addresses.Count);
        WriteUInt16(buffer, 0);
        WriteUInt16(buffer, 0);

        WriteName(buffer, question.Name);
        WriteUInt16(buffer, question.Type);
        WriteUInt16(buffer, question.Class);

        foreach (var address in addresses)
        {
            WriteARecord(buffer, question.Name, ClassIn, ttl, address);
        }
        return buffer.ToArray();
    }

    private static void WriteARecord(List<byte> buffer, string name, ushort recordClass, uint ttl, IPAddress address)
    {
        WriteName(buffer, name);
        WriteUInt16(buffer, TypeA);
        WriteUInt16(buffer, recordClass);
        WriteUInt32(buffer, ttl);
        var bytes = address.GetAddressBytes();
        WriteUInt16(buffer, (ushort)bytes.Length);
        buffer.AddRange(bytes);
    }

    private static void WriteNsecRecord(List<byte> buffer, string name, ushort recordClass, uint ttl)
    {
        WriteName(buffer, name);
        WriteUInt16(buffer, TypeNsec);
        WriteUInt16(buffer, recordClass);
        WriteUInt32(buffer, ttl);

        var rdata = new List<byte>(40);
        WriteName(rdata, name);          // "next domain name" = the name itself
        rdata.Add(0);                    // window block 0
        rdata.Add(1);                    // bitmap length: 1 byte
        rdata.Add(0x40);                 // bit 1 set = type A exists
        WriteUInt16(buffer, (ushort)rdata.Count);
        buffer.AddRange(rdata);
    }

    private static string? ReadName(ReadOnlySpan<byte> data, ref int offset)
    {
        var labels = new StringBuilder();
        int position = offset;
        int hops = 0;
        bool jumped = false;

        while (true)
        {
            if (position >= data.Length) return null;
            int length = data[position];

            if (length == 0)
            {
                position++;
                break;
            }

            if ((length & 0xC0) == 0xC0)
            {
                if (position + 1 >= data.Length || ++hops > 8) return null;
                int pointer = ((length & 0x3F) << 8) | data[position + 1];
                if (!jumped) offset = position + 2;
                jumped = true;
                position = pointer;
                continue;
            }

            if (length > 63 || position + 1 + length > data.Length) return null;
            if (labels.Length > 0) labels.Append('.');
            labels.Append(Encoding.ASCII.GetString(data.Slice(position + 1, length)));
            position += 1 + length;
            if (labels.Length > 253) return null;
        }

        if (!jumped) offset = position;
        return labels.ToString();
    }

    private static void WriteName(List<byte> buffer, string name)
    {
        foreach (var label in name.Split('.', StringSplitOptions.RemoveEmptyEntries))
        {
            var bytes = Encoding.ASCII.GetBytes(label);
            buffer.Add((byte)Math.Min(bytes.Length, 63));
            buffer.AddRange(bytes.Take(63));
        }
        buffer.Add(0);
    }

    private static ushort ReadUInt16(ReadOnlySpan<byte> data, int offset)
        => (ushort)((data[offset] << 8) | data[offset + 1]);

    private static void WriteUInt16(List<byte> buffer, ushort value)
    {
        buffer.Add((byte)(value >> 8));
        buffer.Add((byte)(value & 0xFF));
    }

    private static void WriteUInt32(List<byte> buffer, uint value)
    {
        buffer.Add((byte)(value >> 24));
        buffer.Add((byte)((value >> 16) & 0xFF));
        buffer.Add((byte)((value >> 8) & 0xFF));
        buffer.Add((byte)(value & 0xFF));
    }
}
