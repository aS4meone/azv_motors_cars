# app/glonassoft_api/utils.py
from typing import Any, Iterable
import re


def extract_sensor_value(items: Iterable[dict[str, Any]], substr: str) -> str | None:
    """
    Возвращает value первого сенсора, имя которого содержит substr
    (без учёта регистра). Игнорирует пустые строки и «Данных нет».
    """
    substr = substr.lower()
    for it in items:
        name = (it.get("name") or "").lower()
        value = (it.get("value") or "").strip()
        if substr in name and value and "данн" not in value.lower():
            return value
    return None


def parse_numeric(value: str) -> float:
    """
    Парсит числовое значение из строки, извлекая первое найденное число.
    Обрабатывает строки вида "47.8 л", "51.3%", "100" и т.д.
    """
    if not value:
        return 0.0
    # Ищем первое число в строке (включая десятичные)
    m = re.search(r'[-+]?\d*\.?\d+', value.replace(",", "."))
    return float(m.group()) if m else 0.0
