"""语料摄入(spec #46 A):真源语料文件 → 切块 → 嵌入 → Milvus + PG 投影 + 批次台账。

模块地图:
- ``schema.py``   语料文件(真源 YAML)的解析与文档/切块形状
- ``chunking.py`` 切块与标识派生(纯函数)
- ``pipeline.py`` 摄入编排(替身可注入)
- ``pdf.py``      pypdf 逐页抽取(freeze 用)
"""
