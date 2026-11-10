# A Msg matched against a Wifi constructor.
class Msg:
    Ping = ()
    Pong = (object,)


class Wifi:
    initW = ()
    stationW = (object,)


def handle(x):
    match x:
        case Ping:
            return 0
        case stationW(n):
            return n


def main():
    return handle(Ping)
