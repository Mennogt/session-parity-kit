"""Губернатор пар «аккаунт + профиль».

Замеры показали: платформа разрешает вход паре с ограниченной частотой и
исчерпывает кредит доверия после серии успехов. Значит сервис обязан
считать эту частоту сам — иначе он сжигает аккаунты и получает гейт на
каждой попытке.

Правила:
  * не чаще одного обращения к паре за `pair_min_interval`;
  * после гейта — экспоненциальная пауза `pair_cooldown_base * 2^n`;
  * после успеха пауза не сбрасывается в ноль, а возвращается к базовой;
  * ожидание не расходует попытку задания (задание возвращается в очередь).
"""

from __future__ import annotations

from dataclasses import dataclass

from core.statuses import Status
from store.store import Store


@dataclass(slots=True)
class PairDecision:
    allowed: bool
    wait_seconds: float = 0.0
    reason: str = ""


class RiskGovernor:
    def __init__(
        self,
        store: Store,
        min_interval: float = 180.0,
        cooldown_base: float = 300.0,
        cooldown_max: float = 3600.0,
    ) -> None:
        self.store = store
        self.min_interval = min_interval
        self.cooldown_base = cooldown_base
        self.cooldown_max = cooldown_max

    def check(self, login: str, profile_id: str, now: float) -> PairDecision:
        row = self.store.pair_get(login, profile_id)
        if row is None:
            return PairDecision(True, 0.0, "пара новая")
        cooldown_until = row.get("cooldown_until") or 0.0
        if cooldown_until > now:
            return PairDecision(
                False, cooldown_until - now,
                f"пауза после отказов ({row.get('failures', 0)})",
            )
        last = row.get("last_attempt") or 0.0
        elapsed = now - last
        if elapsed < self.min_interval:
            return PairDecision(
                False, self.min_interval - elapsed,
                f"интервал между входами ({int(elapsed)} из {int(self.min_interval)} с)",
            )
        return PairDecision(True, 0.0, "можно")

    def record(self, login: str, profile_id: str, status: Status, now: float) -> None:
        row = self.store.pair_get(login, profile_id) or {"failures": 0}
        failures = int(row.get("failures") or 0)
        cooldown_until: float | None = None

        if status is Status.SUCCESS:
            failures = 0
        elif status in (Status.CHALLENGE_FAILED, Status.RATE_LIMITED):
            failures += 1
            delay = min(self.cooldown_max, self.cooldown_base * (2 ** (failures - 1)))
            cooldown_until = now + delay
        elif status is Status.PROXY_ERROR:
            # Виноват адрес, а не пара: пауза не растёт.
            pass

        self.store.pair_record(
            login=login,
            profile_id=profile_id,
            last_attempt=now,
            last_success=now if status is Status.SUCCESS else row.get("last_success"),
            failures=failures,
            cooldown_until=cooldown_until,
        )
