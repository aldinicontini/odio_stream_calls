#!/usr/bin/env bash

# Args:
AUDIOFILE=$1
TEST_FLAG=$2

# Path to this project
PATH_PROJECT="/root/odio_stream_calls"

# Determine the call direction based on the file name
if [[ "$AUDIOFILE" == *"-in.wav" ]]; then
    DIRECTION="inbound"
elif [[ "$AUDIOFILE" == *"-out.wav" ]]; then
    DIRECTION="outbound"
else
    echo "Error: the audio file must end with -in.wav or -out.wav"
    exit 1
fi

source "$PATH_PROJECT/venv/bin/activate"

# Run the Python script with the correct arguments
# The --test flag is passed only if provided
#echo "python3 "$PATH_PROJECT/stream_socket.py" "$AUDIOFILE" "$DIRECTION" $TEST_FLAG"
python3 "$PATH_PROJECT/stream_socket.py" "$AUDIOFILE" "$DIRECTION" $TEST_FLAG

# finish
deactivate
