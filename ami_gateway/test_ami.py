import asyncio
import sys
import os

# Ensure the project root is in the Python path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from ami_gateway import AMIClient

async def test_connection():
    print("--- AMI Gateway Connection Test ---")
    client = AMIClient()
    
    print(f"Configured AMI parameters:")
    print(f" - Host: {client.host}")
    print(f" - Port: {client.port}")
    print(f" - Username: {client.username}")
    print(f" - Secret: {'*' * len(client.secret) if client.secret else 'None'}")
    
    print("\nAttempting to connect to Asterisk AMI...")
    try:
        # Run connect with a timeout of 5 seconds
        success = await asyncio.wait_for(client.connect(), timeout=5.0)
        if success:
            print("\n[OK] Connection established successfully!")
            
            # Send a Ping action to test communication
            print("Sending 'Ping' action to Asterisk AMI...")
            try:
                pong = await client.send_action({"Action": "Ping"})
                print(f"[OK] Ping response received: {pong}")
            except Exception as e:
                print(f"[ERROR] Failed to send Ping action: {e}")
            finally:
                await client.disconnect()
        else:
            print("\n[FAILED] Connection returned False (could not establish connection).")
            print("Please verify your Asterisk configuration and check if the AMI port is accessible.")
    except asyncio.TimeoutError:
        print("\n[TIMEOUT] Connection attempt timed out after 5.0 seconds.")
        print("This is normal if no local Asterisk instance is running on this machine.")
        print("Please verify the AMI_HOST and AMI_PORT values in your .env file.")
    except Exception as e:
        print(f"\n[EXCEPTION] An unexpected error occurred: {e}")

if __name__ == "__main__":
    asyncio.run(test_connection())
