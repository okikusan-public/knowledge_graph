"""
GraphRAG共通設定
各プロジェクトから import して使う。環境変数で上書き可能。

Usage:
  from knowledge_graph.config import get_config
  cfg = get_config()  # デフォルト設定
  cfg = get_config(project="project_a")  # プロジェクト指定
"""

import os

# プロジェクト別設定
# 新規プロジェクト追加時はここにエントリを追加する
PROJECTS = {
    "default": {
        "neo4j_uri": "bolt://localhost:7689",
        "neo4j_user": "neo4j",
        "neo4j_password": "changeme",
        "embed_url": "http://localhost:8082/embed",
    },
    # 例:
    # "project_a": {
    #     "neo4j_uri": "bolt://localhost:7687",
    #     "neo4j_user": "neo4j",
    #     "neo4j_password": "changeme",
    #     "embed_url": "http://localhost:8082/embed",
    # },
    # "project_b": {
    #     "neo4j_uri": "bolt://localhost:7688",
    #     "neo4j_user": "neo4j",
    #     "neo4j_password": "your_password",
    #     "embed_url": "http://localhost:8082/embed",
    # },
}

# 共通設定
CHUNK_SIZE = 512
CHUNK_OVERLAP = 50
CHAR_PER_TOKEN = 1.5
EMBEDDING_MODEL = "intfloat/multilingual-e5-base"
EMBEDDING_DIM = 768


class Config:
    def __init__(self, project="default"):
        proj = PROJECTS.get(project, PROJECTS["default"])
        self.project = project
        self.neo4j_uri = os.getenv("NEO4J_URI", proj["neo4j_uri"])
        self.neo4j_user = os.getenv("NEO4J_USER", proj["neo4j_user"])
        self.neo4j_password = os.getenv("NEO4J_PASSWORD", proj["neo4j_password"])
        self.neo4j_auth = (self.neo4j_user, self.neo4j_password)
        self.embed_url = os.getenv("EMBED_URL", proj["embed_url"])
        self.chunk_size = int(os.getenv("CHUNK_SIZE", CHUNK_SIZE))
        self.chunk_overlap = int(os.getenv("CHUNK_OVERLAP", CHUNK_OVERLAP))
        self.char_per_token = float(os.getenv("CHAR_PER_TOKEN", CHAR_PER_TOKEN))

    def __repr__(self):
        return f"Config(project={self.project}, neo4j={self.neo4j_uri})"


def get_config(project=None):
    """設定を取得。project未指定時は環境変数 GRAPHRAG_PROJECT を参照。"""
    if project is None:
        project = os.getenv("GRAPHRAG_PROJECT", "default")
    return Config(project)
