package `in`.kharcha.app.data

/** Category taxonomy (PROJECT_SPEC §12). Must match `kharcha_common.categories.Category`. */
enum class Category(val label: String) {
    FOOD_DELIVERY("Food delivery"),
    DINING_OUT("Dining out"),
    QUICK_COMMERCE_SNACKS("Quick commerce / snacks"),
    GROCERIES("Groceries"),
    SHOPPING("Shopping"),
    ENTERTAINMENT("Entertainment"),
    SUBSCRIPTIONS("Subscriptions"),
    TRAVEL("Travel"),
    TRANSPORT("Transport"),
    BILLS_UTILITIES("Bills & utilities"),
    RENT("Rent"),
    HEALTH("Health"),
    EDUCATION("Education"),
    TRANSFERS("Transfers"),
    CASH_UNCATEGORIZED("Cash (uncategorized)"),
    OTHER("Other"),
    ;

    companion object {
        fun labelOf(name: String): String = entries.firstOrNull { it.name == name }?.label ?: name
    }
}
