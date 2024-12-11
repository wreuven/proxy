import socket
import select
import json
import logging
import argparse

# Logging configuration
logging.basicConfig(level=logging.DEBUG, format='%(asctime)s - %(levelname)s - %(message)s')

# Configuration (load from JSON file or dictionary)
CONFIG = {
    "local": {
        "udp_ports": [],
        "tcp_ports": [],
        "multicast_ports": []
    },
    "remote": {
        "udp_ports": [12345],
        "tcp_ports": [],
        "multicast_ports": [
            {"ip": "239.192.0.1", "port": 30001}
        ]
    },
    "tunnel": {
        "ip": "0.0.0.0",
        "port": 10000
    }
}

# Header format helper functions
def create_header(command, protocol, src_ip, src_port, dest_ip, dest_port, length):
    """
    Create a header as a serialized JSON string.
    """
    return json.dumps({
        "command": command,
        "protocol": protocol,
        "src_ip": src_ip,
        "src_port": src_port,
        "dest_ip": dest_ip,
        "dest_port": dest_port,
        "length": length
    })

def parse_header(header_str):
    """
    Parse a header from a serialized JSON string.
    """
    return json.loads(header_str)

class Proxy:
    def __init__(self, config, role, connect_address):
        self.config = config
        self.role = role
        self.connect_address = connect_address
        self.tunnel_conn = None
        self.connection_map = {}  # Maps (src_ip, src_port) to UDP sockets
        self.buffers = {}  # Per-socket buffers for incoming data
        self.inputs = []  # List of sockets to monitor for readability

    def start(self):
        """
        Main loop to handle all proxy operations.
        """
        outputs = []

        # Setup tunnel connection or listener
        if self.connect_address:
            tunnel_ip, tunnel_port = self.connect_address.split(":")
            tunnel_port = int(tunnel_port)
            self.tunnel_conn = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self.tunnel_conn.connect((tunnel_ip, tunnel_port))
            logging.info(f"Connected to tunnel at {tunnel_ip}:{tunnel_port}")
        else:
            tunnel_ip = self.config["tunnel"]["ip"]
            tunnel_port = self.config["tunnel"]["port"]
            server_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            server_sock.bind((tunnel_ip, tunnel_port))
            server_sock.listen(1)
            logging.info(f"Listening for tunnel connections on {tunnel_ip}:{tunnel_port}")
            self.tunnel_conn, addr = server_sock.accept()
            logging.info(f"Tunnel connection established with {addr}")

        self.inputs.append(self.tunnel_conn)
        self.buffers[self.tunnel_conn] = b""  # Initialize buffer for the tunnel

        # Setup UDP, TCP, and Multicast listeners
        udp_sockets = []
        tcp_listen_sockets = []
        multicast_sockets = []

        for port in self.config[self.role]["udp_ports"]:
            udp_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
            udp_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            udp_sock.bind(("0.0.0.0", port))
            logging.info(f"Listening for UDP on port {port}")
            udp_sockets.append(udp_sock)
            self.inputs.append(udp_sock)

        for entry in self.config[self.role]["multicast_ports"]:
            multicast_ip = entry["ip"]
            multicast_port = entry["port"]
            multicast_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
            multicast_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            multicast_sock.bind(("0.0.0.0", multicast_port))

            # Join multicast group
            mreq = socket.inet_aton(multicast_ip) + socket.inet_aton("0.0.0.0")
            multicast_sock.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP, mreq)
            multicast_sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_LOOP, 1)
            multicast_sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 255)

            logging.info(f"Joined multicast group {multicast_ip} on port {multicast_port}")
            multicast_sockets.append(multicast_sock)
            self.inputs.append(multicast_sock)

        for port in self.config[self.role]["tcp_ports"]:
            tcp_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            tcp_sock.bind(("0.0.0.0", port))
            tcp_sock.listen(5)
            tcp_listen_sockets.append(tcp_sock)
            self.inputs.append(tcp_sock)
            logging.info(f"Listening for TCP on port {port}")

        # Main select loop
        while True:
            readable, writable, exceptional = select.select(self.inputs, outputs, self.inputs)

            for s in readable:
                if s in udp_sockets or s in multicast_sockets or s in self.connection_map.values():
                    # Read data from UDP or multicast socket
                    data, addr = s.recvfrom(4096)
                    if s in self.connection_map.values():
                        # Determine the reverse mapping (find src_ip:src_port for this socket)
                        reverse_mapping = {v: k for k, v in self.connection_map.items()}
                        if s in reverse_mapping:
                            src_ip, src_port = reverse_mapping[s]
                            header = create_header("DATA", "UDP", "0.0.0.0", s.getsockname()[1], src_ip, src_port, len(data))
                            self.send_to_tunnel(header, data)
                            logging.info(f"Forwarded UDP response to tunnel for {src_ip}:{src_port}")
                    else:
                        # Handle regular UDP/multicast logic
                        port = s.getsockname()[1]
                        header = create_header("DATA", "UDP", addr[0], addr[1], "0.0.0.0", port, len(data))
                        self.send_to_tunnel(header, data)

                elif s is self.tunnel_conn:
                    try:
                        data = self.tunnel_conn.recv(4096)
                        if not data:
                            logging.warning("Tunnel connection closed.")
                            self.inputs.remove(self.tunnel_conn)
                            self.tunnel_conn = None
                            continue

                        self.buffers[self.tunnel_conn] += data

                        while b"\n" in self.buffers[self.tunnel_conn]:
                            header_data, self.buffers[self.tunnel_conn] = self.buffers[self.tunnel_conn].split(b"\n", 1)
                            header = parse_header(header_data.decode(errors='ignore'))
                            if header["protocol"] == "UDP":
                                self.handle_udp_tunnel_message(header, self.buffers[self.tunnel_conn][:header["length"]])
                                self.buffers[self.tunnel_conn] = self.buffers[self.tunnel_conn][header["length"]:]
                            elif header["protocol"] == "TCP":
                                self.handle_tcp_tunnel_message(header, self.buffers[self.tunnel_conn][:header["length"]])
                                self.buffers[self.tunnel_conn] = self.buffers[self.tunnel_conn][header["length"]:]

                    except socket.error as e:
                        logging.error(f"Error reading from tunnel: {e}")
                        self.inputs.remove(self.tunnel_conn)
                        self.tunnel_conn = None

    def handle_udp_tunnel_message(self, header, payload):
        """
        Process a UDP message received from the tunnel.
        """
        src_ip = header["src_ip"]
        src_port = header["src_port"]

        # Check if we already have a socket mapped to this src_ip:src_port
        if (src_ip, src_port) not in self.connection_map:
            # Create a new UDP socket for outgoing messages to src_ip:src_port
            udp_out_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            udp_out_sock.bind(("0.0.0.0", 0))  # Bind to a random available port
            self.connection_map[(src_ip, src_port)] = udp_out_sock
            self.buffers[udp_out_sock] = b""  # Initialize buffer
            self.inputs.append(udp_out_sock)  # Add the new socket to the inputs
            logging.info(f"Created new UDP mapping for {src_ip}:{src_port}")

        # Send the payload to the destination IP and port
        udp_out_sock = self.connection_map[(src_ip, src_port)]
        udp_out_sock.sendto(payload, (src_ip, src_port))
        logging.info(f"Forwarded UDP payload to {src_ip}:{src_port}")

    def send_to_tunnel(self, header, data):
        """
        Send a message to the remote proxy through the tunnel.
        """
        if not self.tunnel_conn:
            logging.error("Tunnel connection is not established.")
            return

        try:
            message = header + "\n" + data.decode(errors='ignore')
            self.tunnel_conn.sendall(message.encode())
        except socket.error as e:
            logging.error(f"Failed to send data to tunnel: {e}")
            self.tunnel_conn = None  # Reset tunnel connection


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Start the proxy.")
    parser.add_argument("--role", choices=["local", "remote"], required=True, help="Specify if this proxy is local or remote.")
    parser.add_argument("--connect", help="Optional IP:PORT to connect to a remote tunnel.")
    args = parser.parse_args()

    role = args.role
    connect_address = args.connect

    proxy = Proxy(CONFIG, role, connect_address)
    proxy.start()
