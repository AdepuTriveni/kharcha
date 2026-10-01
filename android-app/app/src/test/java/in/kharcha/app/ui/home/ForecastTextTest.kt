package `in`.kharcha.app.ui.home

import `in`.kharcha.app.sync.ForecastDto
import java.time.LocalDate
import kotlinx.serialization.json.Json
import org.junit.Assert.assertEquals
import org.junit.Test

class ForecastTextTest {
    private val today = LocalDate.of(2026, 10, 1)

    @Test
    fun rangeFromPessimisticToOptimistic() {
        val f = ForecastDto(status = "OK", brokeP20 = "2026-10-14", brokeP50 = "2026-10-18", brokeP80 = "2026-10-23", horizonDays = 45)
        assertEquals("14 Oct – 23 Oct", rangeText(f, today))
        assertEquals("12 Oct", rangeText(f.copy(brokeP20 = "2026-10-12", brokeP80 = "2026-10-12"), today))
        assertEquals("14 Oct – after 15 Nov", rangeText(f.copy(brokeP80 = null), today))
    }

    @Test
    fun decodesBackendResponse() {
        val body = """{"status":"OK","moneyNowPaise":110000,"bankPaise":90000,"cashPaise":20000,
            "balanceFresh":true,"brokeP20":"2026-10-12","brokeP50":"2026-10-12","brokeP80":"2026-10-12",
            "daysLeftP50":11,"probBroke":1.0,"horizonDays":45,"dailySpendP50Paise":10000,
            "topCategories":[{"category":"FOOD_DELIVERY","dailyAvgPaise":10000}],"whatIf":null}"""
        val f = Json { ignoreUnknownKeys = true }.decodeFromString(ForecastDto.serializer(), body)
        assertEquals(11, f.daysLeftP50)
        assertEquals(10_000L, f.topCategories.single().dailyAvgPaise)
    }
}
