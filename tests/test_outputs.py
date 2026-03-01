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
    res_no_auth = subprocess.run(["redis-cli", "ping"], capture_output=True, text=True)
    assert "NOAUTH" in res_no_auth.stdout or "NOAUTH" in res_no_auth.stderr or "Authentication required" in res_no_auth.stdout or "Authentication required" in res_no_auth.stderr, "Security violation: Redis can be accessed without a password!"
    res_with_auth = run_redis_cmd("ping")
    assert "PONG" in res_with_auth.stdout, "Redis server is not running or auth failed with the correct password."

def test_02_supervisor_active():
    assert "RUNNING" in subprocess.run(["supervisorctl", "status", "consumer"], capture_output=True, text=True).stdout

def test_03_health_check_port_8080():
    req = urllib.request.urlopen("http://localhost:8080", timeout=2)
    assert req.read().decode().strip() == "OK"

def test_04_security_and_privileges():
    """Verify all privilege separation and security constraints from the prompt."""
    # 1. 验证用户 mq-worker 是否创建
    res_user = subprocess.run(["id", "-u", "mq-worker"], capture_output=True, text=True)
    assert res_user.returncode == 0, "Requirement failed: System user 'mq-worker' does not exist."

    # 2. 验证 consumer.py 是否以 mq-worker 身份运行
    res_proc = subprocess.run(["pgrep", "-u", "mq-worker", "-f", "consumer.py"], capture_output=True)
    assert res_proc.returncode == 0, "Requirement failed: The consumer process is NOT running as the 'mq-worker' user."

    # 3. 验证 /etc/redis_secret 的 400 权限和 root:root 归属
    secret_file = Path("/etc/redis_secret")
    assert secret_file.exists(), "/etc/redis_secret does not exist."
    stat_res = subprocess.run(["stat", "-c", "%a:%U:%G", "/etc/redis_secret"], capture_output=True, text=True)
    assert stat_res.stdout.strip() == "400:root:root", f"Security violation! /etc/redis_secret permissions/owner should be strictly '400:root:root', but got '{stat_res.stdout.strip()}'"
# =======================================================================

def test_05_normal_processing():
    if not LOG_FILE.exists():
        LOG_FILE.touch()
        subprocess.run(["chown", "mq-worker:mq-worker", str(LOG_FILE)])
    LOG_FILE.write_text("")
    run_redis_cmd("rpush", "task_queue", "NormalTask_1")
    time.sleep(2)
    assert "NormalTask_1" in LOG_FILE.read_text()

def test_06_crash_recovery():
    run_redis_cmd("rpush", "task_queue", "CRASH")
    time.sleep(4)
    assert "RUNNING" in subprocess.run(["supervisorctl", "status", "consumer"], capture_output=True, text=True).stdout
    run_redis_cmd("rpush", "task_queue", "Task_AfterCrash")
    time.sleep(2)
    assert "Task_AfterCrash" in LOG_FILE.read_text()

def test_07_redis_downtime_resilience():
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

def test_08_logrotate_resilience():
    """Chaos Engineering: Verify system survives external log file rotation (FileNotFound & Permission stripping)."""

    # =====================================================================
    # 场景 1: 严格验证 FileNotFoundError
    # 物理删除文件，模拟 logrotate 移走旧日志后，还没来得及建新日志的真空期
    # =====================================================================
    subprocess.run(["rm", "-f", "/var/log/consumer.log"])

    # 此时发送任务，消费者将面临 FileNotFoundError（或在 Python open 'a' 模式下尝试自行建文件）
    run_redis_cmd("rpush", "task_queue", "Task_FileNotFound")
    time.sleep(2)

    # 断言 1：进程绝对不能崩溃
    assert "RUNNING" in subprocess.run(["supervisorctl", "status", "consumer"], capture_output=True, text=True).stdout, "Consumer crashed when encountering FileNotFoundError (missing log file)!"

    # 断言 2：文件应该被恢复，且任务没有丢失
    content = LOG_FILE.read_text() if LOG_FILE.exists() else ""
    assert "Task_FileNotFound" in content, "Failed to recover and write logs when log file was deleted (FileNotFoundError)."

    # =====================================================================
    # 场景 2: 严格验证 PermissionError 与 "gracefully retry" (重试机制)
    # 模拟新建了日志文件，但权限属于 root，mq-worker 无法写入
    # =====================================================================
    subprocess.run(["touch", "/var/log/consumer.log"])
    subprocess.run(["chown", "root:root", "/var/log/consumer.log"])
    subprocess.run(["chmod", "600", "/var/log/consumer.log"])

    # 此时发送任务，消费者必定抛出 PermissionError
    run_redis_cmd("rpush", "task_queue", "Task_PermissionError")
    time.sleep(2)

    # 断言 3：进程在 PermissionError 的持续打击下必须存活
    assert "RUNNING" in subprocess.run(["supervisorctl", "status", "consumer"], capture_output=True, text=True).stdout, "Consumer crashed when encountering PermissionError!"

    # 模拟系统管理员或后续脚本修复了权限
    subprocess.run(["chown", "mq-worker:mq-worker", "/var/log/consumer.log"])
    subprocess.run(["chmod", "666", "/var/log/consumer.log"])

    # 给消费者几秒钟的重试 (retry) 时间
    time.sleep(3)

    # 断言 4：核心验证！之前因权限失败的那个任务，必须被成功重试写入，绝不能丢弃！
    content = LOG_FILE.read_text()
    assert "Task_PermissionError" in content, "Task was lost! The consumer did not gracefully retry logging the task after PermissionError."
