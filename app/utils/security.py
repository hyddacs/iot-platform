"""
安全工具函数
"""
import secrets
import string


def generate_random_secret(length: int = 16) -> str:
    """生成随机密钥"""
    alphabet = string.ascii_letters + string.digits
    return ''.join(secrets.choice(alphabet) for _ in range(length))