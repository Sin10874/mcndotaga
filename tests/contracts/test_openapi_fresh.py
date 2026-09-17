"""契约生成物新鲜度：committed openapi.yaml 必须等于 schemas/*.yaml 的合并结果。

比较**解析后的字典**，不比较文本——文本比较会绑定 PyYAML 的输出格式，
依赖升级即误报。也**不要**用 subprocess 调 main()：那会先覆写生成物再比较，
测试将恒绿，正好失去意义。
"""
from contracts.tools.build_openapi import build

def test_openapi_is_freshly_built_from_schemas(contract_doc):
    assert contract_doc == build(), (
        "contracts/openapi.yaml 与 contracts/schemas/*.yaml 不一致——"
        "运行 python -m contracts.tools.build_openapi 并提交生成物")
