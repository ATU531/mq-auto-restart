Objective：Implement a robust message queue consumer system with auto-restart, dynamic health checking, and resilience to external file-system operations.

Requirements：
1.Message Queue Broker
        - The redis-server package is already installed.
        - You must configure Redis to require a password. 
        - Generate a random password and save it to `/etc/redis_secret`.
2.Consumer Script (`/app/consumer.py`)
        - Connect to local Redis and continuously pop tasks from a list named `task_queue`.
        - Write task payloads to `/var/log/consumer.log` followed by a newline.
        - Crash Simulation：If task payload is `"CRASH"`, exit immediately with a non-zero status.
        - **Dynamic Health Check**: Start a background HTTP server on **port 8080**.
          - If Redis connection is working, respond to GET requests with HTTP 200 "OK".
          - If Redis is unreachable, respond with HTTP 503 "Service Unavailable".
        - **Log-Rotation Resilience**: The script must handle scenarios where `/var/log/consumer.log` is suddenly replaced or its permissions are altered by an external `logrotate` process. The consumer MUST NOT crash due to `PermissionError` or `FileNotFoundError` when writing logs. It should gracefully retry logging tasks.
3.Process Management & Auto-Restart
        - Use `supervisor` to manage the `/app/consumer.py` process.
        - The configuration must automatically restart the process if it crashes.
        - **CRITICAL NAME REQUIREMENT**: The program name inside your supervisor configuration MUST be exactly `consumer` (i.e., your configuration file must use the header `[program:consumer]`).
4.Privilege Separation & Security
        - Create a non-root system user named `mq-worker`.
        - The supervisor process MUST run the consumer as the `mq-worker` user.
        - Security Rule: `/etc/redis_secret` MUST have exactly `400` permissions owned by `root:root`. The setup script must pass this password securely without altering these permissions or using `sudo` in the consumer.
5.Execution (`/app/solve.sh`)
        - Write your setup commands in `/app/solve.sh` to initialize the environment and start the supervisor service.
