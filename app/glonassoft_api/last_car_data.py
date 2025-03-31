from typing import Union, List

from app.RateLimitedHTTPClient import RateLimitedHTTPClient


async def get_last_vehicles_data(token: str, ids: List[int]) -> Union[dict, None]:
    """ Получает последние данные по транспортным средствам и сохраняет в файл. """
    url = "https://regions.glonasssoft.ru/api/v3/vehicles/getlastdata"
    headers = {"X-Auth": token, "Content-Type": "application/json"}

    client = RateLimitedHTTPClient.get_instance()
    response = await client.send_request("POST", url, json=ids, headers=headers)

    if response.status_code == 200:
        data = response.json()
        return data

    print(f"Ошибка при получении данных ТС: {response.status_code}")
    return None


async def get_vehicle_data(token: str, vehicle_imei: int) -> Union[dict, None]:
    """ Получает данные о конкретном транспортном средстве и сохраняет в файл. """
    url = f"https://regions.glonasssoft.ru/api/v2.0/monitoringVehicles/devicestatebyimei?imei={vehicle_imei}&timezone=5"
    headers = {"X-Auth": token, "Content-Type": "application/json"}

    client = RateLimitedHTTPClient.get_instance()
    response = await client.send_request("GET", url, headers=headers)

    if response.status_code == 200:
        data = response.json()
        return data

    print(f"Ошибка при получении данных ТС {vehicle_imei}: {response.status_code}")
    return None
