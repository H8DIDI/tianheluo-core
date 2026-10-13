"""可复现的 zip 写入：固定时间戳、固定权限、条目按给定顺序写入。
同样的内容永远得到逐字节相同的包——没改动的插件不会在 git 里产生“假改动”，用户端的校验值也稳定。"""
import pathlib
import zipfile

FIXED = (2026, 1, 1, 0, 0, 0)


def create(path):
    return zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED)


def _info(name):
    zi = zipfile.ZipInfo(name, FIXED)
    zi.compress_type = zipfile.ZIP_DEFLATED
    zi.external_attr = 0o644 << 16
    zi.create_system = 0
    return zi


def writestr(z, name, data):
    z.writestr(_info(name), data.encode("utf-8") if isinstance(data, str) else data)


def write(z, src, name):
    writestr(z, name, pathlib.Path(src).read_bytes())
