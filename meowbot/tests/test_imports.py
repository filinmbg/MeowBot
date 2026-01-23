def test_imports():
    # якщо імпорти падають — дерево/модулі зібрані неправильно
    from meowbot.core.domain.types import Bar, Signal, Trade
    from meowbot.core.domain.enums import Side, TradeStatus, SignalAction

    assert Bar and Signal and Trade
    assert Side.LONG.value == "LONG"
    assert TradeStatus.OPEN.value == "OPEN"
    assert SignalAction.HOLD.value == "HOLD"
