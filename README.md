# Odio Stream Calls

This project provides a Python-based solution to process audio files from calls and stream them over sockets. It is designed to integrate with **Asterisk** and handle both inbound and outbound calls.

## Features

- Automatically detects call direction based on audio file name (`-in.wav` → inbound, `-out.wav` → outbound).  
- Supports a **test mode** using the `--test` flag.  
- Can be executed from Asterisk using a simple Bash wrapper.  
- Runs inside a Python virtual environment for clean dependency management.

## Project Structure

odio_stream_calls/
├── stream_socket.py # Main Python script that streams audio
├── run_stream.sh # Bash wrapper to activate venv and run Python
├── bin/ # Virtual environment
└── README.md


## Prerequisites

- Python 3.10+  
- Virtualenv installed (`python3 -m venv /root/odio_stream_calls/bin`)  
- Required Python packages installed inside the virtual environment  

Example:

```bash
cd /root/odio_stream_calls
source bin/activate
pip install -r requirements.txt


Usage
From the command line
./run_stream.sh <AUDIOFILE> [--test]


<AUDIOFILE>: Path to the .wav file (must end in -in.wav or -out.wav)
--test (optional): Run the script in test mode

Examples:
./run_stream.sh 1762560249.55706-in.wav
./run_stream.sh 1762560249.55706-out.wav --test

From Asterisk
In your dialplan, you can call the Bash wrapper like:

exten => s,n,Set(AUDIOFILE=/var/spool/asterisk/monitor/${CALLFILENAME}.wav)
exten => s,n,System(/root/odio_stream_calls/run_stream.sh ${AUDIOFILE} --test)


The script will automatically detect the call direction and stream the audio.

How It Works
The Bash script detects if the call is inbound or outbound based on the file suffix.

Activates the Python virtual environment.
Runs stream_socket.py with the audio file, direction, and optional --test flag.

Deactivates the virtual environment.

Notes
Only .wav files ending with -in.wav or -out.wav are supported.
Ensure the virtual environment is correctly set up with all required dependencies.
For Asterisk integration, make sure the audio file path is accessible to the script.