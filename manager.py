import pika
import docker
import os
import uuid  # For generating unique container names
import json
import time

client = docker.from_env()

# Pre-built base Docker image for running user code, speeds up building new containers
BASE_IMAGE = "baseimage"

# Execution timeout limit (in seconds)
CONTAINER_TIMEOUT = 10

def print_header(message):
    print("\n" + "=" * 70)
    print(f"### {message.upper()} ###")

def print_divider():
    print("-" * 60)

def run_test_case(user_code, test_case_inputs, expected_output):
    """Runs user code against a single test case inside a Docker container and captures high-precision execution time."""
    container_name = f"container_{uuid.uuid4().hex}"
    script_filename = f"script_{container_name}.py"

    def is_number(s):
        try:
            float(s)
            return True
        except ValueError:
            return False

    # Adjusted input assignment to properly handle strings and numbers
    input_assignments = "\n".join([f"input{i+1} = {test_case_inputs[i]}" if isinstance(test_case_inputs[i], (int, float)) or (isinstance(test_case_inputs[i], str) and is_number(test_case_inputs[i]))
                                   else f"input{i+1} = '{test_case_inputs[i]}'"
                                   for i in range(len(test_case_inputs))])

    # Get the last line of the user's code (assuming it's the function call)
    last_line = user_code.strip().splitlines()[-1]

    # Combine the user code and input assignments into the final script
    full_code = f"""
import time
import sys

# Redirect all print statements to devnull (suppress them)
class DevNull:
    def write(self, msg):
        pass

sys.stdout = DevNull()

# Assign inputs
{input_assignments}

# Start the high-resolution timer
start_time = time.perf_counter()

# User's function definition
{user_code}

# Execute the last line (assumed to be the function call)
result = {last_line}
end_time = time.perf_counter()

# Restore sys.stdout after capturing the result
sys.stdout = sys.__stdout__

# Print the result explicitly
print(result)

execution_time_ms = (end_time - start_time) * 1000  # Convert to milliseconds

# Print execution time in milliseconds
print(f"Execution Time: {{execution_time_ms:.3f}} ms")
"""


    # Write the full code (user's function + input assignments) to the script file
    with open(script_filename, "w") as script_file:
        script_file.write(full_code)

    try:
        print(f"🚀 Running Docker container {container_name}...")

        # Run the Docker container using the pre-built base image and mounting the script file
        container = client.containers.run(
            image=BASE_IMAGE,
            command="python /code/script.py",
            detach=True,
            network_mode="none",
            mem_limit="512m",
            cpu_quota=50000,
            volumes={os.path.abspath(script_filename): {'bind': '/code/script.py', 'mode': 'ro'}},
        )

        # Wait for the container to finish with a timeout
        start_time = time.perf_counter()
        while time.perf_counter() - start_time < CONTAINER_TIMEOUT:
            container_status = container.wait(timeout=1)
            if container_status['StatusCode'] == 0:
                break

        # If the container has not finished within the timeout, kill it
        if time.perf_counter() - start_time >= CONTAINER_TIMEOUT:
            print(f"⏰ Timeout reached. Killing container {container_name}...")
            container.kill()
            return False, f"Timeout on test case with input: {test_case_inputs}"

        # Retrieve output from Docker logs
        output = container.logs().decode('utf-8').strip()

        # Log the output for debugging purposes
        print(f"TEST Output from Docker logs: {output}")

        # Extract execution time from the output
        execution_time_line = [line for line in output.splitlines() if "Execution Time:" in line]
        if execution_time_line:
            execution_time = execution_time_line[0].split(":")[-1].strip()

        # Normalize both output and expected output for comparison
        normalized_output = output.splitlines()[0].strip()  # First line is the function result
        normalized_expected_output = str(expected_output).strip()

        print(f"Comparing output: '{normalized_output}' with expected: '{normalized_expected_output}'")

        # Return the result and the high-precision execution time
        if normalized_output == normalized_expected_output:
            return True, f"Test case passed (Execution Time: {execution_time})"
        else:
            return False, f"Expected: '{normalized_expected_output}', but got: '{normalized_output}' (Execution Time: {execution_time})"

    except docker.errors.ContainerError as e:
        error_message = e.stderr.decode('utf-8')
        print(f"❌ Error:\n{error_message}")
        return False, f"Error during execution: {error_message}"

    finally:
        # Clean up the container and the temporary user script file
        if container:
            container.remove(force=True)
        if os.path.exists(script_filename):
            os.remove(script_filename)


def execute_user_code(user_code, user_id, test_cases):
    print(f"USER: {user_id} | Processing test cases...")

    for index, test_case in enumerate(test_cases, start=1):
        test_case_inputs = test_case['inputs']
        expected_output = test_case['expected_output']

        print(f"Running test case {index}: inputs = {test_case_inputs}, expected output = {expected_output}")

        # Run the user's code against the current test case
        passed, message = run_test_case(user_code, test_case_inputs, expected_output)

        if not passed:
            # If any test case fails, return the failed test case result
            return f"Test case {index} failed: {message}"

    # If all test cases pass
    return "All test cases passed!"

def callback(ch, method, properties, body):
    """Callback function to process incoming messages from RabbitMQ"""
    try:
        message = json.loads(body.decode('utf-8'))
        user_code = message['usercode']
        user_id = message['userid']
        test_cases = message.get('test_cases', [])

        print_header(f"RECEIVED CODE TO EXECUTE FOR USER: {user_id}")

        # Execute the user code against the provided test cases
        result = execute_user_code(user_code, user_id, test_cases)

        # Send the result back (you can integrate this with a WebSocket or result queue)
        print(result)

    except json.JSONDecodeError:
        print("❌ Received an invalid JSON message.")
    except KeyError as e:
        print(f"❌ Missing expected key in JSON message: {e}")
    except Exception as e:
        print(f"❌ An error occurred while processing the message: {e}")
    finally:
        # Acknowledge message after processing
        ch.basic_ack(delivery_tag=method.delivery_tag)

def start_microservice():
    connection = pika.BlockingConnection(pika.ConnectionParameters('localhost'))
    channel = connection.channel()

    # Declare the queue (it will be created if it doesn't exist)
    channel.queue_declare(queue='code_queue', durable=True)

    # Set up a consumer on the queue
    channel.basic_consume(queue='code_queue', on_message_callback=callback)

    print_header("WAITING FOR MESSAGES. TO EXIT PRESS CTRL+C")
    try:
        channel.start_consuming()
    except KeyboardInterrupt:
        print_header("SHUTTING DOWN...")
        channel.stop_consuming()
    finally:
        connection.close()

if __name__ == "__main__":
    start_microservice()
