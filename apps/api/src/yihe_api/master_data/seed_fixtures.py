"""Packaged synthetic partner fixtures projected from T010 master_data.json."""

PARTNERS = {
    "PARTY-C01": ("上海示例客户01", "CUSTOMER", True, "c01@example.invalid"),
    "PARTY-C02": ("上海示例客户02", "CUSTOMER", True, "c02@example.invalid"),
    "PARTY-C03": ("上海示例客户03", "CUSTOMER", True, "c03@example.invalid"),
    "PARTY-C04": ("上海示例客户04", "CUSTOMER", True, "c04@example.invalid"),
    "PARTY-C05": ("上海示例客户05", "CUSTOMER", True, "c05@example.invalid"),
    "PARTY-C06": ("上海示例客户06", "CUSTOMER", True, "c06@example.invalid"),
    "PARTY-C07": ("上海示例客户07", "CUSTOMER", True, "c07@example.invalid"),
    "PARTY-C08": ("上海示例客户08", "CUSTOMER", True, "c08@example.invalid"),
    "PARTY-C09": ("上海示例客户09", "CUSTOMER", False, "c09@example.invalid"),
    "PARTY-DUAL": ("上海示例客户10", "BOTH", True, "c10@example.invalid"),
    "PARTY-V01": ("上海示例供应商01", "SUPPLIER", True, "v01@example.invalid"),
    "PARTY-V02": ("上海示例供应商02", "SUPPLIER", True, "v02@example.invalid"),
    "PARTY-V03": ("上海示例供应商03", "SUPPLIER", True, "v03@example.invalid"),
    "PARTY-V04": ("上海示例供应商04", "SUPPLIER", True, "v04@example.invalid"),
}


CUSTOMERS = {
    "PARTY-C01": ("C01", "standard", True),
    "PARTY-C02": ("C02", "credit", True),
    "PARTY-C03": ("C03", "prepaid", True),
    "PARTY-C04": ("C04", "standard", True),
    "PARTY-C05": ("C05", "standard", True),
    "PARTY-C06": ("C06", "standard", True),
    "PARTY-C07": ("C07", "standard", True),
    "PARTY-C08": ("C08", "standard", True),
    "PARTY-C09": ("C09", "standard", False),
    "PARTY-DUAL": ("C10", "dual", True),
}


SUPPLIERS = {
    "PARTY-V01": ("V01", "HARDWARE"),
    "PARTY-V02": ("V02", "DISTRIBUTOR"),
    "PARTY-V03": ("V03", "DISTRIBUTOR"),
    "PARTY-V04": ("V04", "SOFTWARE_SERVICE"),
    "PARTY-DUAL": ("V05", "GENERAL"),
}
