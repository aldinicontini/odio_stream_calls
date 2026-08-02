import os
import asyncio
from dotenv import load_dotenv
from panoramisk import Manager
from utils.app_debugger import init_debugger

load_dotenv()

# Logger setup using the app's debugging utility
LOG_FILE_CONNECTIONS = os.getenv('LOG_FILE_CONNECTIONS', './connections.log')
logging = init_debugger(LOG_FILE_CONNECTIONS)

class AMIClient:
    """
    Gateway connector for Asterisk Manager Interface (AMI).
    Uses the asynchronous `panoramisk` library.
    """
    def __init__(self, host=None, port=None, username=None, secret=None):
        self.host = host or os.getenv('AMI_HOST', '127.0.0.1')
        self.port = int(port or os.getenv('AMI_PORT', '5038'))
        self.username = username or os.getenv('AMI_USERNAME', 'admin')
        self.secret = secret or os.getenv('AMI_SECRET', 'password')
        self.manager = None

    async def connect(self):
        """
        Establishes connection to Asterisk AMI.
        """
        logging.info(f"Connecting to Asterisk AMI at {self.host}:{self.port} with user '{self.username}'...")
        try:
            self.manager = Manager(
                host=self.host,
                port=self.port,
                username=self.username,
                secret=self.secret
            )
            await self.manager.connect()
            logging.info("Connected to Asterisk AMI successfully.")
            return True
        except Exception as e:
            logging.error(f"Failed to connect to Asterisk AMI: {e}")
            return False

    async def disconnect(self):
        """
        Closes the AMI connection.
        """
        if self.manager:
            logging.info("Closing Asterisk AMI connection...")
            try:
                self.manager.close()
                logging.info("Asterisk AMI connection closed.")
            except Exception as e:
                logging.error(f"Error during Asterisk AMI disconnection: {e}")

    async def send_action(self, action):
        """
        Sends an AMI Action asynchronously and returns the response.
        """
        if not self.manager:
            raise RuntimeError("AMIClient is not connected. Call connect() first.")
        
        try:
            action_name = action.get('Action', 'Unknown')
            logging.info(f"Sending AMI action: '{action_name}'...")
            response = await self.manager.send_action(action)
            logging.info(f"Action '{action_name}' response: {response}")
            return response
        except Exception as e:
            logging.error(f"Failed to execute AMI action: {e}")
            raise

    async def originate_spy(self, spy_channel, target_extension, context="spy-context", caller_id="SpyTool <888>", priority="1"):
        """
        Originates a call to spy on a target extension.
        
        Args:
            spy_channel (str): The channel representing the spying supervisor (e.g. 'PJSIP/201').
            target_extension (str): The extension to spy on (e.g. '101' or a pre-configured spy prefix).
            context (str): The dialplan context containing ChanSpy instructions.
            caller_id (str): Optional caller ID to show on the supervisor's phone.
            priority (str): Priority in the target context.
        """
        action = {
            'Action': 'Originate',
            'Channel': spy_channel,
            'Context': context,
            'Exten': target_extension,
            'Priority': priority,
            'CallerID': caller_id,
            'Async': 'true'
        }
        return await self.send_action(action)
