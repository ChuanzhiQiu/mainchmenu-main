"""Filtros de formato numérico para MainchApp.

`intdot` formatea un número con puntos como separador de miles y coma como
separador decimal (formato chileno), p. ej. 4500 -> "4.500", 267906 -> "267.906".
"""
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

from django import template

register = template.Library()


@register.filter
def intdot(value, decimals=0):
    """Formatea un número con puntos de miles (y coma decimal si decimals > 0)."""
    try:
        decimals = int(decimals)
    except (TypeError, ValueError):
        decimals = 0

    try:
        d = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return value

    quantum = Decimal("1") if decimals <= 0 else Decimal("0." + "0" * decimals)
    d = d.quantize(quantum, rounding=ROUND_HALF_UP)

    sign = "-" if d < 0 else ""
    d = abs(d)

    if decimals > 0:
        s = f"{d:.{decimals}f}"
        int_part, dec_part = s.split(".")
    else:
        int_part = str(int(d))
        dec_part = ""

    # Agrupar miles con punto
    grupos = []
    while int_part:
        grupos.insert(0, int_part[-3:])
        int_part = int_part[:-3]

    result = sign + ".".join(grupos)
    if dec_part:
        result += "," + dec_part
    return result
