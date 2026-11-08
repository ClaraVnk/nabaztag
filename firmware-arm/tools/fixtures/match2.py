class Msg:
    Ping = ()
    Pong = (int,)


def handle(x):
    match x:
        case Ping:
            return 1
        case _:
            return 0


def main():
    return handle(Ping)
