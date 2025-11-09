#!/bin/bash

# Args:
AUDIOFILE=$1
TEST_FLAG=$2

# Path to this project
PATH_PROJECT="/usr/local/bin/odio_stream_calls"
echo "event_started" >> "/usr/local/bin/odio_stream_calls/connection.log"
echo "venv: source $PATH_PROJECT/venv/bin/activate" >> "/usr/local/bin/odio_stream_calls/connection.log"

source "$PATH_PROJECT/venv/bin/activate"
# Run the Python script with the correct arguments
# The --test flag is passed only if provided
#echo "python3 "$PATH_PROJECT/stream_socket.py" "$AUDIOFILE" "$DIRECTION" $TEST_FLAG"
echo "command python3 '$PATH_PROJECT/stream_socket.py' '$AUDIOFILE' $TEST_FLAG" >> "/usr/local/bin/odio_stream_calls/connection.log"
$PATH_PROJECT/venv/bin/python3 "$PATH_PROJECT/stream_socket.py" "$AUDIOFILE" $TEST_FLAG

# finish
deactivate
