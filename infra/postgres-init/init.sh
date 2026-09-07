#!/bin/sh
# 首次启动创建 Langfuse 独立库(业务库 mae 由 POSTGRES_DB 创建)
psql -U postgres -c "CREATE DATABASE langfuse;"
