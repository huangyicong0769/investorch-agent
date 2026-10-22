from rqalpha.api import get_position, history_bars, order_target_percent, update_universe


def init(context):
    context.stocks = ["600000.XSHG", "000001.XSHE"]
    context.last_month = None
    context.target = None
    update_universe(context.stocks)


def before_trading(context):
    if (context.now.year, context.now.month) == context.last_month:
        return
    scores = []
    for stock in context.stocks:
        closes = history_bars(stock, 61, "1d", "close", skip_suspended=False, include_now=False, adjust_type="pre")
        if len(closes) != 61 or closes[0] <= 0:
            raise ValueError("Insufficient completed-bar momentum history for " + stock)
        scores.append((float(closes[-1] / closes[0] - 1), stock))
    score, winner = max(scores)
    context.target = winner if score > 0 else None


def handle_bar(context, bar_dict):
    month = (context.now.year, context.now.month)
    if month == context.last_month:
        return
    for stock in context.stocks:
        if stock != context.target and get_position(stock).quantity > 0:
            order_target_percent(stock, 0.0)
    if context.target is not None:
        order_target_percent(context.target, 0.95)
    context.last_month = month
