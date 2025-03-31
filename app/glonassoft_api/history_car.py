import asyncio
import urllib.parse
import time
from functools import wraps
from httpx import Response
from app.RateLimitedHTTPClient import RateLimitedHTTPClient  # Импортируем класс


def measure_time(func):
    @wraps(func)
    async def wrapper(*args, **kwargs):
        start_time = time.time()
        result = await func(*args, **kwargs)
        end_time = time.time()
        execution_time = end_time - start_time
        print(f"Функция '{func.__name__}' выполнилась за {execution_time:.6f} секунд")
        return result

    return wrapper


async def fetch_fuel_data(geo_cache_key, auth_token):
    print("вызвалось")
    url = f"https://regions.glonasssoft.ru/api/history/maininfoasync/{geo_cache_key}"
    headers = {"X-Auth": auth_token}
    client = RateLimitedHTTPClient.get_instance()

    response: Response = await client.send_request("GET", url, headers=headers)
    if response.status_code != 200:
        print(f"Ошибка при запросе топлива: {response.status_code}")
        return None, None

    try:
        data = response.json()
        trips = data.get("Data", {}).get("trips", [])
        if trips:
            return trips[0].get("FStart", "Нет данных"), trips[0].get("FEnd", "Нет данных")
    except Exception as e:
        print(f"Ошибка обработки топлива: {e}")

    return None, None


@measure_time
async def fetch_gps_coordinates_async(device_id, start_date, end_date, auth_token):
    client = RateLimitedHTTPClient.get_instance()
    headers = {"X-Auth": auth_token}
    base_url = "https://regions.glonasssoft.ru/api/history/primaryinfoasync"
    params = {
        "id": device_id, "from": start_date, "to": end_date,
        "needGeo": "true", "unionTime": "60", "unionDist": "100",
        "filterTime": "60", "timeZoneHoursOffset": "5",
        "addressFormat": "[House] [Street] [City] [Country]",
        "showStoppingEvents": "false", "showNearestObjects": "false"
    }
    request_url = f"{base_url}?{urllib.parse.urlencode(params)}"

    response: Response = await client.send_request("GET", request_url, headers=headers)
    if response.status_code != 200:
        print(f"Ошибка запроса: {response.status_code}")
        return None

    try:
        request_data = response.json()
        request_id = request_data.get("RequestId")
        if not request_id:
            print("Нет RequestId в ответе")
            return None

        state_url = f"https://regions.glonasssoft.ru/api/history/state/{request_id}"
        max_attempts, attempt, wait_time = 30, 0, 0.01

        while attempt < max_attempts:
            await asyncio.sleep(wait_time)
            attempt += 1
            state_response: Response = await client.send_request("GET", state_url, headers=headers)

            if state_response.status_code != 200:
                wait_time = min(2.0, wait_time * 1.5)
                continue

            state_data = state_response.json()
            status, progress = state_data.get("Status"), state_data.get("ProgressValue", "N/A")
            geo_cache_key = state_data.get("Data", {}).get("geoCacheKey")

            if attempt % 3 == 0 or status != "InProgress":
                print(f"Статус: {status}, Прогресс: {progress}%")

            if status == "Success":
                points_data = state_data.get("Data", {}).get("points", {}).get("data", "")
                if not points_data:
                    print("Нет данных о координатах")
                    return None

                coordinates = []
                for entry in points_data.split(":"):
                    parts = entry.split(",")
                    if len(parts) >= 2:
                        try:
                            coord_data = {"lat": float(parts[0]), "lon": float(parts[1])}
                            if len(parts) > 2:
                                coord_data["altitude"] = float(parts[2])
                            if len(parts) > 4:
                                coord_data["timestamp"] = int(parts[4])
                            coordinates.append(coord_data)
                        except (ValueError, IndexError):
                            continue

                f_start, f_end = await fetch_fuel_data(geo_cache_key, auth_token) if geo_cache_key else (None, None)

                return {
                    "device_id": device_id, "period": {"start": start_date, "end": end_date},
                    "count": len(coordinates), "coordinates": coordinates,
                    "fuel": {"start": f_start, "end": f_end}
                }

            if status == "Failed":
                print("Запрос не удался")
                return None

            wait_time = 1.0 if progress != 'N/A' and progress < 50 else 0.5

        print("Превышено число попыток ожидания")
        return None

    except Exception as e:
        print(f"Ошибка обработки ответа: {e}")
        return None
