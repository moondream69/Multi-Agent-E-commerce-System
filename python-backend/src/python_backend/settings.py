"""配置加载:沿用仓库根目录 .env,变量名与 NestJS 版一致。"""

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

_REPO_ROOT = Path(__file__).resolve().parents[3]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=_REPO_ROOT / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Postgres(.env 用 DB_USERNAME)
    db_host: str = "localhost"
    db_port: int = 5432
    db_username: str = "postgres"
    db_password: str = "postgres"
    db_name: str = "multi_agent_ecommerce"

    # Redis
    redis_host: str = "localhost"
    redis_port: int = 6379

    # LLM(OpenAI 兼容协议)
    llm_api_key: str = ""
    llm_api_url: str = "https://api.deepseek.com"
    llm_model: str = "deepseek-v4-flash"
    llm_max_concurrency: int = 2  # 进程内并发上限(DeepSeek 账号级限流防护)

    # Embedding(Ollama)
    embedding_api_url: str = "http://localhost:11434"
    embedding_model: str = "bge-m3"
    embedding_dimension: int = 1024

    # 认证(JWT + bcrypt,小团队平权)
    auth_jwt_secret: str = "dev-insecure-secret-change-me-0123456789abcdef"
    auth_token_ttl_hours: int = 24
    cors_origins: list[str] = ["http://localhost:5173", "http://127.0.0.1:5173"]

    # 初始管理员(seed 幂等创建;密码留空则随机生成并打印一次)
    auth_admin_username: str = "admin"
    auth_admin_password: str = ""

    # 分级审批护栏
    shadow_mode: bool = False
    approval_ttl_hours: int = 4


settings = Settings()
