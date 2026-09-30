import hashlib
import json
import random
import string
import threading
import time
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

# ============================================================
# 基本配置
# ============================================================

WAIT_INTERVAL = 3
MAX_WAIT_SECONDS = 600
MAX_RETRY = 3
MAX_PARALLEL = 10          # 并行处理的测试点数量

HERE = Path(__file__).resolve().parent
CONFIG_PATH = HERE / "config.json"
TEST_DIR = HERE / "test" / "new"
TEST_DIR.mkdir(parents=True, exist_ok=True)

API_URL = "https://polygon.codeforces.com/api"

# 全局打印锁：保证多线程下日志不会交错
PRINT_LOCK = threading.Lock()


def safe_print(*args, **kwargs):
    """线程安全的 print"""
    with PRINT_LOCK:
        print(*args, **kwargs)


# ============================================================
# API 配置校验与管理
# ============================================================

def random_prefix():
    return "".join(
        random.choice(string.ascii_letters + string.digits) for _ in range(6)
    )


def make_api_signature(method, params, api_secret):
    rand = random_prefix()
    items = sorted(
        [(str(k), str(v)) for k, v in params.items() if k != "apiSig"],
        key=lambda x: (x[0], x[1])
    )
    query = "&".join([f"{k}={v}" for k, v in items])
    sign_string = f"{rand}/{method}?{query}#{api_secret}"
    digest = hashlib.sha512(sign_string.encode("utf-8")).hexdigest()
    return rand + digest


def test_api_key(api_key, api_secret):
    method = "problems.list"
    params = {
        "apiKey": str(api_key),
        "time": str(int(time.time())),
    }
    params["apiSig"] = make_api_signature(method, params, api_secret)

    url = f"{API_URL}/{method}"
    data = urllib.parse.urlencode(params).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=data,
        method="POST",
        headers={
            "Content-Type": "application/x-www-form-urlencoded",
            "User-Agent": "Polygon-Test-Downloader/1.0",
        },
    )

    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            if response.status == 200:
                result = json.loads(response.read().decode("utf-8"))
                return result.get("status") == "OK"
    except Exception:
        return False
    return False


def prompt_and_save_config():
    """提示用户手动输入 API Key 并保存"""
    print()
    print("=" * 60)
    print("                 Polygon API 配置")
    print("=" * 60)
    print("请在 Polygon -> Settings -> API Keys 中生成 Key 和 Secret。")
    print()

    while True:
        api_key = input("API Key    : ").strip()
        api_secret = input("API Secret : ").strip()

        if not api_key or not api_secret:
            print("[错误] API Key 和 Secret 不能为空，请重新输入。\n")
            continue

        print("\n[INFO] 正在验证输入的 API Key……")
        if test_api_key(api_key, api_secret):
            print("[OK] API Key 验证成功！")
            config = {"apiKey": api_key, "apiSecret": api_secret}
            CONFIG_PATH.write_text(
                json.dumps(config, ensure_ascii=False, indent=4), encoding="utf-8"
            )
            print(f"[OK] 配置已保存至：{CONFIG_PATH}")
            return config
        else:
            print("[错误] 验证失败：API Key/Secret 无效或网络问题，请重新输入。\n")


def get_valid_config():
    """获取有效配置：若存在且有效则直接使用，否则提示输入"""
    if CONFIG_PATH.exists():
        try:
            data = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
            api_key = data.get("apiKey", "").strip()
            api_secret = data.get("apiSecret", "").strip()

            if api_key and api_secret:
                print("[INFO] 发现已保存的 config.json，正在验证有效性……")
                if test_api_key(api_key, api_secret):
                    print("[OK] config.json 验证通过，直接使用。")
                    return {"apiKey": api_key, "apiSecret": api_secret}
                else:
                    print("[警告] 已保存的 API Key 已过期或无效。")
        except Exception as e:
            print(f"[警告] 读取 config.json 失败：{e}")

    return prompt_and_save_config()


# ============================================================
# 用户交互：动态输入题目信息
# ============================================================

def prompt_problem_info():
    """提示用户输入需要获取数据的题目信息"""
    print()
    print("=" * 60)
    print("                 输入题目下载信息")
    print("                 下载信息在题目首页")
    print("=" * 60)

    while True:
        problem_id = input("Problem ID (例如 485543): ").strip()
        if problem_id.isdigit():
            break
        print("[错误] Problem ID 必须为数字，请重新输入。")

    # testset = input("Testset (示例 test 注意不要加括号, 直接回车使用默认值): ").strip()
    # if not testset:
    testset = "tests"

    while True:
        try:
            start_idx_str = input("起始测试点序号 (例如 1): ").strip()
            start_idx = int(start_idx_str)
            end_idx_str = input("结束测试点序号 (例如 23): ").strip()
            end_idx = int(end_idx_str)

            if start_idx > 0 and end_idx >= start_idx:
                break
            print("[错误] 请确保起始序号 >= 1 且结束序号 >= 起始序号。")
        except ValueError:
            print("[错误] 测试点序号必须为数字，请重新输入。")

    return {
        "problem_id": str(problem_id),
        "testset": testset,
        "start_index": start_idx,
        "end_index": end_idx,
    }


# ============================================================
# Polygon API 工具函数
# ============================================================

def polygon_api(method, params, config):
    p = {str(k): str(v) for k, v in params.items()}
    p["apiKey"] = config["apiKey"]
    p["time"] = str(int(time.time()))
    p["apiSig"] = make_api_signature(method, p, config["apiSecret"])

    url = f"{API_URL}/{method}"
    data = urllib.parse.urlencode(p).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=data,
        method="POST",
        headers={
            "Content-Type": "application/x-www-form-urlencoded",
            "User-Agent": "Polygon-Test-Downloader/1.0",
        },
    )

    for attempt in range(1, MAX_RETRY + 1):
        try:
            with urllib.request.urlopen(request, timeout=60) as response:
                body = response.read()
                status = response.status

            if status != 200:
                raise RuntimeError(f"HTTP {status}")

            if method in ("problem.testInput", "problem.testAnswer"):
                return body.decode("utf-8", errors="replace")

            text = body.decode("utf-8", errors="replace")
            result = json.loads(text)

            if result.get("status") != "OK":
                comment = result.get("comment", "未知错误")
                raise RuntimeError(f"Polygon API 错误：{comment}")

            return result.get("result")

        except urllib.error.HTTPError as e:
            err_msg = f"HTTP Error {e.code}: {e.reason}"
            try:
                body = e.read().decode("utf-8", errors="replace")
                err_data = json.loads(body)
                if "comment" in err_data:
                    err_msg += f" ({err_data['comment']})"
            except Exception:
                pass
            safe_print(f"[API ERROR] {method} 第 {attempt}/{MAX_RETRY} 次：{err_msg}")
            if attempt >= MAX_RETRY:
                raise RuntimeError(err_msg)
            time.sleep(2 * attempt)
        except Exception as e:
            safe_print(f"[API ERROR] {method} 第 {attempt}/{MAX_RETRY} 次：{e}")
            if attempt >= MAX_RETRY:
                raise
            time.sleep(2 * attempt)


# ============================================================
# 业务逻辑
# ============================================================

def get_tests(problem_info, config):
    return polygon_api(
        "problem.tests",
        {"problemId": problem_info["problem_id"], "testset": problem_info["testset"]},
        config,
    ) or []


def get_previews(problem_info, config):
    return polygon_api(
        "problem.previewTests",
        {"problemId": problem_info["problem_id"], "testset": problem_info["testset"]},
        config,
    ) or []


def find_preview(previews, index):
    for item in previews:
        if int(item.get("testIndex", -1)) == index:
            return item
    return None


def wait_for_test(index, problem_info, config):
    safe_print(f"\n[GENERATE] 请求 Polygon 生成 test{index}")
    start_time = time.time()
    first_request = True

    while True:
        elapsed = time.time() - start_time
        if elapsed > MAX_WAIT_SECONDS:
            raise TimeoutError(f"test{index} 生成超过 {MAX_WAIT_SECONDS} 秒。")

        try:
            previews = get_previews(problem_info, config)
            preview = find_preview(previews, index)

            if preview is None:
                safe_print(f"[WAIT] test{index}: 尚未返回 preview")
            else:
                generated_input = preview.get("input")
                generated_answer = preview.get("answer")

                if generated_input is None or generated_answer is None:
                    safe_print(f"[WAIT] test{index}: 正在生成……")
                else:
                    if isinstance(generated_input, str) and "error" in generated_input.lower():
                        raise RuntimeError(f"Polygon preview 生成失败：{generated_input}")

                    safe_print(f"[READY] test{index}: 生成完成")
                    return

        except Exception as e:
            safe_print(f"[WAIT ERROR] test{index}: {e}")
            if first_request:
                raise

        first_request = False
        time.sleep(WAIT_INTERVAL)


def download_test(index, problem_info, config):
    safe_print(f"[DOWNLOAD] 获取 test{index} 输入与答案……")

    # 1. 下载输入文件 (.in)
    input_content = polygon_api(
        "problem.testInput",
        {
            "problemId": problem_info["problem_id"],
            "testset": problem_info["testset"],
            "testIndex": index,
        },
        config,
    )
    if not isinstance(input_content, str):
        raise RuntimeError(f"test{index} 输入数据格式异常。")

    input_file = TEST_DIR / f"{index:02d}.in"
    input_file.write_text(input_content, encoding="utf-8")

    # 2. 下载答案文件 (.out)
    ans_content = polygon_api(
        "problem.testAnswer",
        {
            "problemId": problem_info["problem_id"],
            "testset": problem_info["testset"],
            "testIndex": index,
        },
        config,
    )
    if not isinstance(ans_content, str):
        raise RuntimeError(f"test{index} 答案数据格式异常。")

    ans_file = TEST_DIR / f"{index:02d}.out"
    ans_file.write_text(ans_content, encoding="utf-8")

    return input_file, len(input_content), ans_file, len(ans_content)


# ============================================================
# 并行任务：单测试点处理
# ============================================================

def process_test(index, problem_info, config, available_indices):
    """
    处理单个测试点：等待生成 -> 下载 .in / .out
    返回 (index, success: bool, err: str | None)
    本函数只读取共享数据（problem_info / config / available_indices），
    不修改任何共享状态，因此多线程下安全。
    """
    if available_indices and index not in available_indices:
        safe_print(f"[SKIP] Polygon 中不存在 test{index}")
        return index, False, "test 不存在"

    try:
        wait_for_test(index, problem_info, config)
        in_file, in_len, ans_file, ans_len = download_test(index, problem_info, config)
        safe_print(
            f"\n[OK] test{index}\n"
            f"     输入：{in_file} ({in_len} 字符)\n"
            f"     答案：{ans_file} ({ans_len} 字符)"
        )
        return index, True, None
    except Exception as e:
        safe_print(f"\n[FAIL] test{index}\n       {e}")
        return index, False, str(e)


# ============================================================
# 主程序入口
# ============================================================

def main():
    print()
    print("=" * 60)
    print("          Polygon 实时测试数据获取器")
    print("=" * 60)

    try:
        # 1. 验证或获取 API Key 配置
        config = get_valid_config()

        # 2. 提示用户输入要下载的题目信息
        problem_info = prompt_problem_info()

    except KeyboardInterrupt:
        print("\n\n[INFO] 用户取消操作。")
        return

    print("\n" + "=" * 60)
    print("                 正在开始任务")
    print("=" * 60)
    print(f"Problem ID : {problem_info['problem_id']}")
    print(f"Testset    : {problem_info['testset']}")
    print(f"Range      : {problem_info['start_index']} ~ {problem_info['end_index']}")
    print(f"Output     : {TEST_DIR}")
    print(f"Parallel   : {MAX_PARALLEL}\n")

    print("[INFO] 正在连接 Polygon API 获取测试用例列表……")
    try:
        tests = get_tests(problem_info, config)
    except Exception as e:
        print(f"\n[ERROR] 无法获取题目信息，请检查 Problem ID 是否存在及 API 权限：{e}")
        return

    print(f"[OK] Polygon 返回 {len(tests)} 个测试。")

    available_indices = set()
    for test in tests:
        try:
            available_indices.add(int(test.get("index")))
        except Exception:
            pass

    indices = list(range(problem_info["start_index"], problem_info["end_index"] + 1))
    success = 0
    failed = 0

    print(f"\n[INFO] 启动 {MAX_PARALLEL} 个线程并行处理 {len(indices)} 个测试点……")

    # 使用 with 保证线程池一定被关闭，回收 worker 线程与队列，避免资源/内存泄漏
    executor = ThreadPoolExecutor(
        max_workers=MAX_PARALLEL, thread_name_prefix="polygon"
    )
    try:
        future_to_index = {
            executor.submit(
                process_test, idx, problem_info, config, available_indices
            ): idx
            for idx in indices
        }

        for future in as_completed(future_to_index):
            try:
                _, ok, _err = future.result()
            except Exception as e:
                # 兜底：process_test 内部已捕获，这里再保护一次
                safe_print(f"[FAIL] 任务异常：{e}")
                ok = False
            if ok:
                success += 1
            else:
                failed += 1
    except KeyboardInterrupt:
        print("\n[INFO] 收到中断，正在取消未开始的任务并等待运行中的任务结束……")
        # cancel_futures=True 只在 Py3.9+ 支持
        try:
            executor.shutdown(wait=True, cancel_futures=True)
        except TypeError:
            executor.shutdown(wait=True)
        print("[INFO] 已安全退出。")
        return
    finally:
        # 确保线程池一定被回收（幂等调用）
        executor.shutdown(wait=True)

    print("\n" + "=" * 60)
    print("                     完成")
    print("=" * 60)
    print(f"成功：{success}\n失败：{failed}\n输出目录：{TEST_DIR}\n")


if __name__ == "__main__":
    main()

    '''
    485543
    tests
    1
    23
    '''