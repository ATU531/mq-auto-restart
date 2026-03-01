import subprocess, time, urllib.request, urllib.error
from pathlib import Path

LOG_FILE = Path("/var/log/consumer.log")

def get_redis_pass():
    try:
        return Path("/etc/redis_secret").read_text().strip()
    except:
        return ""

def run_redis_cmd(*args):
    password = get_redis_pass()
    cmd = ["redis-cli"]
    if password:
        cmd.extend(["-a", password])
    cmd.extend(list(args))
    return subprocess.run(cmd, capture_output=True, text=True)

def test_01_redis_is_running_and_secured():
    """Verify that Redis is running AND enforcing password authentication."""
    # 1. 裸连测试：不带密码直接 ping，必须被拒绝
    res_no_auth = subprocess.run(["redis-cli", "ping"], capture_output=True, text=True)
    # 无论是旧版还是新版 Redis，没密码通常会返回 NOAUTH 或 Authentication required
    assert "NOAUTH" in res_no_auth.stdout or "NOAUTH" in res_no_auth.stderr or "Authentication required" in res_no_auth.stdout or "Authentication required" in res_no_auth.stderr, "Security violation: Redis can be accessed without a password!"
    
    # 2. 授权测试：带上密码 ping，必须返回 PONG
    res_with_auth = run_redis_cmd("ping")
    assert "PONG" in res_with_auth.stdout, "Redis server is not running or auth failed with the correct password."

def test_02_supervisor_active():
    assert "RUNNING" in subprocess.run(["supervisorctl", "status", "consumer"], capture_output=True, text=True).stdout

def test_03_health_check_port_8080():
    req = urllib.request.urlopen("http://localhost:8080", timeout=2)
    assert req.read().decode().strip() == "OK"

def test_04_normal_processing():
    if not LOG_FILE.exists(): 
        LOG_FILE.touch()
        subprocess.run(["chown", "mq-worker:mq-worker", str(LOG_FILE)])
    LOG_FILE.write_text("")
    run_redis_cmd("rpush", "task_queue", "NormalTask_1")
    time.sleep(2)
    assert "NormalTask_1" in LOG_FILE.read_text()

def test_05_crash_recovery():
    run_redis_cmd("rpush", "task_queue", "CRASH")
    time.sleep(4)
    assert "RUNNING" in subprocess.run(["supervisorctl", "status", "consumer"], capture_output=True, text=True).stdout
    run_redis_cmd("rpush", "task_queue", "Task_AfterCrash")
    time.sleep(2)
    assert "Task_AfterCrash" in LOG_FILE.read_text()

def test_06_redis_downtime_resilience():
    subprocess.run(["killall", "-9", "redis-server"])
    subprocess.run(["rm", "-f", "/var/run/redis/redis-server.pid"])
    time.sleep(2)
    
    try:
        urllib.request.urlopen("http://localhost:8080", timeout=2)
        assert False, "Health check should have failed with 503."
    except urllib.error.HTTPError as e:
        assert e.code == 503
    
    subprocess.run(["service", "redis-server", "start"])
    time.sleep(3)
    
    req = urllib.request.urlopen("http://localhost:8080", timeout=2)
    assert req.read().decode().strip() == "OK"
        
    run_redis_cmd("rpush", "task_queue", "Task_AfterRedisRestart")
    time.sleep(2)
    assert "Task_AfterRedisRestart" in LOG_FILE.read_text()

def test_07_logrotate_resilience():
    """Chaos Engineering: Verify system survives external log file rotation (Permission stripping)."""
    # 模拟 logrotate：重命名旧日志，创建属于 root 的新日志文件，剥夺 mq-worker 的写入权限
    subprocess.run(["mv", "/var/log/consumer.log", "/var/log/consumer.log.bak"])
    subprocess.run(["touch", "/var/log/consumer.log"])
    subprocess.run(["chown", "root:root", "/var/log/consumer.log"])
    subprocess.run(["chmod", "600", "/var/log/consumer.log"])
    
    # 此时发送任务，消费者将遇到 PermissionError
    run_redis_cmd("rpush", "task_queue", "Task_During_LogRotate")
    time.sleep(2)
    
    # 检查进程是否因为写日志失败而崩溃
    assert "RUNNING" in subprocess.run(["supervisorctl", "status", "consumer"], capture_output=True, text=True).stdout, "Consumer crashed when encountering log permission error!"
    
    # 恢复权限（模拟 logrotate 或系统管理员的后续修复）
    subprocess.run(["chown", "mq-worker:mq-worker", "/var/log/consumer.log"])
    subprocess.run(["chmod", "666", "/var/log/consumer.log"])
    time.sleep(2)
    
    # 验证消费者是否恢复写入，并且之前的任务没有丢失（如果它实现了重试或没有崩溃）
    run_redis_cmd("rpush", "task_queue", "Task_After_LogRotate")
    time.sleep(2)
    content = LOG_FILE.read_text()
    assert "Task_After_LogRotate" in content, "Failed to recover and write logs after logrotate simulation."
