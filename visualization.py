import matplotlib.pyplot as plt
import matplotlib.animation as animation


class Visualizer:

    def __init__(self, simulation):

        self.sim = simulation
        self.fig, self.ax = plt.subplots()
        self.ax.set_xlim(-1, self.sim.graph.width)
        self.ax.set_ylim(-1, self.sim.graph.height)

    def draw(self):

        self.ax.clear()

        graph = self.sim.graph

        for node in graph.obstacles:

            x, y = graph.get_position(node)

            self.ax.scatter(
                x,
                y,
                marker="s",
                s=400,
                color="black"
            )

        for node_id, (x, y) in graph.nodes.items():

            if node_id in graph.obstacles:
                continue

            self.ax.scatter(
                x,
                y,
                color="lightgray",
                s=10
            )

        import matplotlib.cm as cm
        colors = cm.tab10.colors

        for agent in self.sim.agents:

            if agent.task is None:
                continue

            task = agent.task
            color = colors[agent.id % 10]

            if not task.delivered:

                x, y = graph.get_position(task.delivery)

                self.ax.scatter(
                    x,
                    y,
                    marker="s",
                    s=200,
                    edgecolors=color,
                    facecolors="none",
                    linewidths=2
                )

            if not task.picked_up:

                x, y = graph.get_position(task.pickup)

                self.ax.scatter(
                    x,
                    y,
                    marker="x",
                    s=200,
                    color=color,
                    linewidths=2
                )

        for agent in self.sim.agents:

            x, y = graph.get_position(agent.node)

            color = colors[agent.id % 10]

            self.ax.scatter(
                x,
                y,
                s=250,
                color=color
            )

        self.ax.set_title(f"Time: {self.sim.time}")

        self.ax.set_xticks([])
        self.ax.set_yticks([])

        self.ax.set_aspect("equal")

    def frame_generator(self):

        while not self.sim.all_finished():

            self.sim.step()

            yield self.sim.time

    def save_gif(self, filename="simulation.gif", fps=2):

        ani = animation.FuncAnimation(
            self.fig,
            lambda frame: self.draw(),
            frames=self.frame_generator(),
            repeat=False,
            cache_frame_data=False
        )

        writer = animation.PillowWriter(fps=fps)

        ani.save(filename, writer=writer)

        print("Zapisano GIF:", filename)