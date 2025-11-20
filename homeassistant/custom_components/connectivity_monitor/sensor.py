from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from homeassistant.components.sensor import SensorEntity, SensorEntityDescription, SensorStateClass
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import PERCENTAGE
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.helpers.device_registry import DeviceInfo

from .const import DEFAULT_NAME, DOMAIN
from .coordinator import ConnectivityDataCoordinator


@dataclass
class ConnectivitySensorDescription(SensorEntityDescription):
    value_fn: Callable[[ConnectivityDataCoordinator], Any] | None = None


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coordinator: ConnectivityDataCoordinator = hass.data[DOMAIN][entry.entry_id]

    descriptions: list[ConnectivitySensorDescription] = [
        ConnectivitySensorDescription(
            key="last_loss_pct",
            name=f"{DEFAULT_NAME} Last Loss",
            native_unit_of_measurement=PERCENTAGE,
            value_fn=lambda c: (c.latest_record or {}).get("loss_pct"),
            state_class=SensorStateClass.MEASUREMENT,
        ),
        ConnectivitySensorDescription(
            key="last_rtt_ms",
            name=f"{DEFAULT_NAME} Last RTT",
            native_unit_of_measurement="ms",
            value_fn=lambda c: (c.latest_record or {}).get("rtt_avg_ms"),
            state_class=SensorStateClass.MEASUREMENT,
        ),
        ConnectivitySensorDescription(
            key="last_target",
            name=f"{DEFAULT_NAME} Last Target",
            value_fn=lambda c: (c.latest_record or {}).get("target")
            or (c.latest_record or {}).get("dst_host"),
            icon="mdi:target-variant",
        ),
        ConnectivitySensorDescription(
            key="last_public_ip",
            name=f"{DEFAULT_NAME} Last Public IP",
            value_fn=lambda c: (c.latest_record or {}).get("public_ip"),
            icon="mdi:ip-network",
        ),
        ConnectivitySensorDescription(
            key="last_source_ip",
            name=f"{DEFAULT_NAME} Last Source IP",
            value_fn=lambda c: (c.latest_record or {}).get("src_ip"),
            icon="mdi:lan",
        ),
        ConnectivitySensorDescription(
            key="last_timestamp",
            name=f"{DEFAULT_NAME} Last Timestamp",
            value_fn=lambda c: (c.latest_record or {}).get("timestamp"),
            icon="mdi:clock-outline",
        ),
        ConnectivitySensorDescription(
            key="mtr_last_loss",
            name=f"{DEFAULT_NAME} Last Hop Loss",
            native_unit_of_measurement=PERCENTAGE,
            value_fn=lambda c: (c.latest_record or {}).get("mtr_last_loss_pct"),
            icon="mdi:chart-line",
        ),
        ConnectivitySensorDescription(
            key="mtr_last_avg",
            name=f"{DEFAULT_NAME} Last Hop RTT",
            native_unit_of_measurement="ms",
            value_fn=lambda c: (c.latest_record or {}).get("mtr_last_avg_ms"),
            icon="mdi:chart-bell-curve",
        ),
        ConnectivitySensorDescription(
            key="mtr_last_hop",
            name=f"{DEFAULT_NAME} Last Hop",
            value_fn=lambda c: (c.latest_record or {}).get("mtr_last_hop"),
            icon="mdi:route",
        ),
        ConnectivitySensorDescription(
            key="daily_uptime_pct",
            name=f"{DEFAULT_NAME} Daily Uptime",
            native_unit_of_measurement=PERCENTAGE,
            value_fn=lambda c: (c.most_recent_day or {}).get("uptime_pct"),
            icon="mdi:clock-check",
        ),
        ConnectivitySensorDescription(
            key="daily_avg_loss",
            name=f"{DEFAULT_NAME} Daily Avg Loss",
            native_unit_of_measurement=PERCENTAGE,
            value_fn=lambda c: (c.most_recent_day or {}).get("avg_loss_pct"),
            icon="mdi:chart-line-variant",
        ),
        ConnectivitySensorDescription(
            key="daily_avg_rtt",
            name=f"{DEFAULT_NAME} Daily Avg RTT",
            native_unit_of_measurement="ms",
            value_fn=lambda c: (c.most_recent_day or {}).get("avg_rtt_ms"),
            icon="mdi:speedometer",
        ),
        ConnectivitySensorDescription(
            key="daily_min_rtt",
            name=f"{DEFAULT_NAME} Daily Min RTT",
            native_unit_of_measurement="ms",
            value_fn=lambda c: (c.most_recent_day or {}).get("min_rtt_ms"),
            icon="mdi:arrow-collapse-down",
        ),
        ConnectivitySensorDescription(
            key="daily_max_rtt",
            name=f"{DEFAULT_NAME} Daily Max RTT",
            native_unit_of_measurement="ms",
            value_fn=lambda c: (c.most_recent_day or {}).get("max_rtt_ms"),
            icon="mdi:arrow-collapse-up",
        ),
    ]

    entities: list[SensorEntity] = [
        ConnectivityMonitorSensor(coordinator, description, entry.entry_id)
        for description in descriptions
    ]

    async_add_entities(entities)


class ConnectivityMonitorSensor(CoordinatorEntity[ConnectivityDataCoordinator], SensorEntity):
    _attr_has_entity_name = True

    def __init__(
        self, coordinator: ConnectivityDataCoordinator, description: ConnectivitySensorDescription, entry_id: str
    ) -> None:
        super().__init__(coordinator)
        self.entity_description = description
        self._attr_unique_id = f"{DOMAIN}_{entry_id}_{description.key}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry_id)},
            name=DEFAULT_NAME,
        )

    @property
    def native_value(self) -> Any:
        if self.entity_description.value_fn is None:
            return None
        return self.entity_description.value_fn(self.coordinator)
