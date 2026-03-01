#!/bin/bash
set -e

pip3 install redis --break-system-packages

echo "1. 配置用户和安全隔离..."
useradd -M -s /bin/false mq-worker
chown mq-worker:mq-worker /var/log/consumer.log

SECRET="Super$ecret$(date +%s)"
echo "$SECRET" > /etc/redis_secret
chmod 400 /etc/redis_secret
chown root:root /etc/redis_secret

echo "2. 启动加密的 Redis..."
sed -i 's/# requirepass foobared/requirepass '"$SECRET"'/' /etc/redis/redis.conf
service redis-server restart

echo "3. 创建高可用消费者脚本..."
cat << 'EOF' > /app/consumer.py
import redis, sys, time, threading, os
from http.server import BaseHTTPRequestHandler, HTTPServer

r_client = None

class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        global r_client
        try:
            if r_client and r_client.ping():
                self.send_response(200)
                self.end_headers()
                self.wfile.write(b"OK\n")
                return
        except:
            pass
        self.send_response(503)
        self.end_headers()
        self.wfile.write(b"Service Unavailable\n")
    def log_message(self, format, *args): pass

def run_health_server():
    server = HTTPServer(('0.0.0.0', 8080), HealthHandler)
    server.serve_forever()

def safe_write_log(task, max_retries=10):
    log_file = '/var/log/consumer.log'
    for _ in range(max_retries):
        try:
            with open(log_file, 'a') as f:
                f.write(task + '\n')
            return
        except (PermissionError, FileNotFoundError):
            # 遭遇 logrotate，优雅等待权限恢复
            time.sleep(1)
    print(f"Failed to write log after retries: {task}", file=sys.stderr)

def main():
    global r_client
    redis_pass = os.environ.get('REDIS_PASS')
    r_client = redis.Redis(host='localhost', port=6379, password=redis_pass, decode_responses=True)
    
    try:
        r_client.ping()
    except:
        sys.exit(1)

    threading.Thread(target=run_health_server, daemon=True).start()
    
    while True:
        try:
            result = r_client.blpop('task_queue', timeout=1)
            if result:
                if result[1] == "CRASH":
                    sys.exit(1)
                safe_write_log(result[1])
        except (redis.ConnectionError, redis.TimeoutError):
            time.sleep(2)

if __name__ == '__main__':
    main()
EOF

echo "4. 配置 Supervisor..."
cat << EOF > /etc/supervisor/conf.d/consumer.conf
[program:consumer]
command=/usr/bin/python3 /app/consumer.py
user=mq-worker
autostart=true
autorestart=true
startretries=3
environment=PYTHONUNBUFFERED=1,REDIS_PASS="${SECRET}"
EOF

service supervisor start
supervisorctl update
supervisorctl start consumer || true
sleep 3
