import heapq


def heuristic(a, b):
    return abs(a[0] - b[0]) + abs(a[1] - b[1])


def space_time_a_star(
    environment,
    start,
    goal,
    reservation_table,
    start_time=0,
    max_time=200
):

    open_set = []
    heapq.heappush(open_set, (0, start, start_time))

    came_from = {}
    g_score = {(start, start_time): 0}

    while open_set:
        _, current, t = heapq.heappop(open_set)

        if current == goal:
            path = []
            state = (current, t)

            while state in came_from:
                pos, time = state
                path.append(pos)
                state = came_from[state]

            path.append(start)
            path.reverse()
            return path

        neighbors = environment.get_neighbors(*current) + [current]  # allow wait

        for neighbor in neighbors:
            next_t = t + 1

            if next_t > max_time:
                continue

            if reservation_table.is_reserved(current, neighbor, next_t):
                continue

            state = (neighbor, next_t)
            tentative_g = g_score[(current, t)] + 1

            if state not in g_score or tentative_g < g_score[state]:
                g_score[state] = tentative_g
                f = tentative_g + heuristic(neighbor, goal)
                came_from[state] = (current, t)
                heapq.heappush(open_set, (f, neighbor, next_t))

    return []