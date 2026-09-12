"""应用配置(宪章 ADR-0005):单源来自环境变量,.env 可选。"""

from __future__ import annotations

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # 单一真源:仓库根 .env(compose env_file 与后端共读)。
    # 顺序语义:pydantic-settings 后读覆盖先读——本地 .env 在前、根在后,根恒胜,
    # 即便未来出现 python-backend/.env 也不遮蔽根(消除双文件漂移)。
    model_config = SettingsConfigDict(env_file=(".env", "../.env"), env_file_encoding="utf-8", extra="ignore")

    # 环境剖面(宪章 Q33):dev=演练(影子可见)/ prod=生产(审批锁死)
    environment: str = "dev"
    # 数据库(业务库 mae;langfuse 库由 postgres 初始化脚本创建)
    database_url: str = "postgresql+psycopg://postgres:postgres@localhost:5432/mae"
    # Redis(LLM 并发限流 + 短时状态)
    redis_host: str = "localhost"
    redis_port: int = 6379
    # LLM(DeepSeek,OpenAI 兼容协议)
    llm_api_key: str = ""
    llm_api_url: str = "https://api.deepseek.com"
    llm_model: str = "deepseek-v4-flash"
    llm_max_concurrency: int = 2
    # Embedding(Ollama bge-m3,1024 维)
    embedding_api_url: str = "http://localhost:11434"
    embedding_model: str = "bge-m3"
    embedding_dimension: int = 1024
    # Milvus Standalone
    milvus_uri: str = "http://localhost:19530"
    # Langfuse(观测层;留空禁用)
    langfuse_host: str = ""
    langfuse_public_key: str = ""
    langfuse_secret_key: str = ""
    # 认证
    auth_jwt_secret: str = "dev-secret-change-me"
    auth_token_ttl_hours: int = 24
    auth_admin_username: str = "admin"
    auth_admin_password: str = ""
    # 审批
    approval_ttl_hours: int = 4
    # 汇率(实时 API,基准 CNY;失效降级用缓存)。基址不含基准币:客户端自行追加 /{base}(issue #29)
    fx_api_url: str = "https://open.er-api.com/v6/latest"
    # 跨域(前端 dev 服务器)
    cors_origins: str = "http://localhost:5173"

    @property
    def shadow_mode(self) -> bool:
        """影子模式 = 环境级配置(宪章 Q33):dev 演练开、prod 生产锁死,运行时不可切换。"""
        return self.environment == "dev"

    @property
    def postgres_dsn(self) -> str:
        """psycopg 直连 DSN(SQLAlchemy URL 去掉 +psycopg 驱动前缀)。"""
        return self.database_url.replace("postgresql+psycopg://", "postgresql://")

    @property
    def cors_origin_list(self) -> list[str]:
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
