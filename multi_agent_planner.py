import heapq


def heuristic(graph, a, b):
    x1, y1 = graph.get_position(a)
    x2, y2 = graph.get_position(b)
    return abs(x1 - x2) + abs(y1 - y2)


def space_time_a_star(graph, start, goal,
                      reservation_table,
                      start_time=0,
                      max_time=500):

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
                node, time = state
                path.append(node)
                state = came_from[state]

            path.append(start)
            path.reverse()
            return path

        neighbors = graph.get_neighbors(current) + [current]

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
                f = tentative_g + heuristic(graph, neighbor, goal)
                came_from[state] = (current, t)
                heapq.heappush(open_set, (f, neighbor, next_t))

    return []