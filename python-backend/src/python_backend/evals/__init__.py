"""Agent 评测(spec #55):金标场景集 → 真实任务产出 → 判据分数。

模块地图:
- ``schema.py`` 评测场景文件(真源 YAML)的解析与场景形状
- ``judge.py``  judge 配置闸(判分客户端见票 #59)
- ``snapshot.py`` 产出快照(切片级产出 + 语料版本锚;本地 JSON,score 的回评输入)
- ``corpus_anchor.py`` 语料版本锚:内容哈希指纹(离线重算)+ 台账批次附记(尽力)
- ``projection.py`` Langfuse 投影:场景集 → dataset、每次运行 → dataset run item(缺密钥显式报错)
- ``runner.py`` ``run`` 编排:哨兵校验 → 合成数据播种 → 逐条串行驱动 → 快照 + 投影
"""
