from pathlib import Path

def get_constant_value(key: str, file_path: Path = Path("constant.txt")) -> str:
    """
    從 constant.txt 撈取指定 key 的值
    格式: key:value
    """
    if not file_path.exists():
        raise FileNotFoundError(f"參數檔不存在: {file_path}")

    with open(file_path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or ":" not in line:
                continue
            k, v = line.split(":", 1)
            if k.strip() == key:
                return v.strip()
def set_constant_value(
    key: str,
    value: str,
    file_path: Path = Path("constant.txt"),
    create_if_missing: bool = True
) -> None:
    """
    修改或新增 constant.txt 中的 key:value

    - 若 key 存在 → 修改其值
    - 若 key 不存在：
        - create_if_missing=True → 新增
        - create_if_missing=False → 丟 KeyError
    """
    lines = []

    # 檔案不存在
    if not file_path.exists():
        if not create_if_missing:
            raise FileNotFoundError(f"參數檔不存在: {file_path}")
        file_path.touch(encoding="utf-8")

    with open(file_path, "r", encoding="utf-8") as f:
        lines = f.readlines()

    found = False
    new_lines = []

    for line in lines:
        raw = line.rstrip("\n")
        if not raw or ":" not in raw:
            new_lines.append(line)
            continue

        k, _ = raw.split(":", 1)
        if k.strip() == key:
            new_lines.append(f"{key}:{value}\n")
            found = True
        else:
            new_lines.append(line)

    if not found:
        if create_if_missing:
            new_lines.append(f"{key}:{value}\n")
        else:
            raise KeyError(f"找不到參數 key: {key}")

    with open(file_path, "w", encoding="utf-8") as f:
        f.writelines(new_lines)