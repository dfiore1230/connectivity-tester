from __future__ import annotations

from homeassistant.components.binary_sensor import BinarySensorEntity, BinarySensorDeviceClass
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DEFAULT_NAME, DOMAIN
from .coordinator import ConnectivityDataCoordinator


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coordinator: ConnectivityDataCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([ConnectivityOnlineBinarySensor(coordinator, entry.entry_id)])


class ConnectivityOnlineBinarySensor(CoordinatorEntity[ConnectivityDataCoordinator], BinarySensorEntity):
    _attr_device_class = BinarySensorDeviceClass.CONNECTIVITY
    _attr_has_entity_name = True
    _attr_name = f"{DEFAULT_NAME} Internet Up"

    def __init__(self, coordinator: ConnectivityDataCoordinator, entry_id: str) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{DOMAIN}_{entry_id}_internet_up"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry_id)},
            name=DEFAULT_NAME,
        )

    @property
    def is_on(self) -> bool | None:
        record = self.coordinator.latest_record
        if not record:
            return None
        loss = record.get("loss_pct")
        try:
            return float(loss) < 100.0
        except (TypeError, ValueError):
            return None
