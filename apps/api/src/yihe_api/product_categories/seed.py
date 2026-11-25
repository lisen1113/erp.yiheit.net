"""Synthetic, brand-neutral category fixture definitions."""

from yihe_api.product_categories.schema import Schema

GROUPS = {
    "COMPUTE": ("计算设备", ["商用笔记本", "台式机", "工作站", "平板电脑"]),
    "DATACENTER": ("服务器与数据中心", ["机架式服务器", "塔式服务器", "存储设备", "网络设备"]),
    "PERIPHERALS": ("显示与外设", ["显示器", "键盘鼠标", "扩展坞", "电源及线材"]),
    "SOFTWARE": ("软件与订阅", ["软件许可证", "按用户订阅", "按设备订阅"]),
    "SERVICES": ("专业服务", ["安装服务", "技术支持", "项目实施"]),
}

FIXTURE_SCHEMAS: dict[tuple[str, int], Schema] = {
    ("COMPUTE", 1): {
        "type": "object",
        "properties": {"memory_gb": {"type": "integer", "title": "内存 GB", "minimum": 8}},
        "required": ["memory_gb"],
    },
    ("DATACENTER", 1): {
        "type": "object",
        "properties": {"rack_units": {"type": "integer", "title": "机架单位", "minimum": 1}},
        "required": ["rack_units"],
    },
    ("SOFTWARE", 2): {
        "type": "object",
        "properties": {
            "billing_cycle": {"type": "string", "title": "计费周期", "enum": ["monthly", "annual"]}
        },
        "required": ["billing_cycle"],
    },
}
