import socket
import argparse
import logging
import time

# Logging configuration
logging.basicConfig(level=logging.DEBUG, format='%(asctime)s - %(levelname)s - %(message)s')

BUFFER_SIZE = 1024
PING_MESSAGE = "Ping"
PONG_MESSAGE = "Pong"

def run_server(bind_ip, bind_port):
    """
    Runs the UDP server.
    """
    server_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    server_sock.bind((bind_ip, bind_port))
    logging.info(f"UDP server listening on {bind_ip}:{bind_port}")

    while True:
        data, addr = server_sock.recvfrom(BUFFER_SIZE)
        message = data.decode()
        logging.info(f"Received message '{message}' from {addr}")

        if message.strip() == PING_MESSAGE:
            response = PONG_MESSAGE.encode()
            server_sock.sendto(response, addr)
            logging.info(f"Sent response '{PONG_MESSAGE}' to {addr}")

def run_client(server_ip, server_port):
    """
    Runs the UDP client.
    """
    client_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    client_sock.settimeout(2)  # Timeout for responses
    logging.info(f"UDP client sending to {server_ip}:{server_port}")

    while True:
        try:
            # Send "Ping" to the server
            message = PING_MESSAGE.encode()
            client_sock.sendto(message, (server_ip, server_port))
            logging.info(f"Sent message '{PING_MESSAGE}' to {server_ip}:{server_port}")

            # Wait for "Pong" response
            data, addr = client_sock.recvfrom(BUFFER_SIZE)
            response = data.decode()
            logging.info(f"Received response '{response}' from {addr}")

        except socket.timeout:
            logging.warning("No response received, retrying...")

        time.sleep(1)  # Send a message every second

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Test UDP server and client for proxy functionality.")
    parser.add_argument("--mode", choices=["client", "server"], required=True, help="Mode to run the program: 'local' for client, 'remote' for server.")
    parser.add_argument("--bind_ip", default="127.0.0.1", help="IP to bind the server to (only for --remote). Default: 127.0.0.1.")
    parser.add_argument("--bind_port", type=int, default=12345, help="Port to bind the server to (only for --remote). Default: 12345.")
    parser.add_argument("--server_ip", default="127.0.0.1", help="IP of the server to send messages to (only for --local). Default: 127.0.0.1.")
    parser.add_argument("--server_port", type=int, default=12345, help="Port of the server to send messages to (only for --local). Default: 12345.")

    args = parser.parse_args()

    if args.mode == "server":
        # Run the server with default bind IP and port
        logging.info(f"Starting in server mode ...")
        run_server(args.bind_ip, args.bind_port)
    elif args.mode == "client":
        # Run the client with default server IP and port
        logging.info(f"Starting in client mode ...")
        run_client(args.server_ip, args.server_port)
