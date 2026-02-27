import matplotlib.pyplot as plt
import matplotlib.animation as animation


class Visualizer:
    def __init__(self, simulation):
        self.sim = simulation
        self.fig, self.ax = plt.subplots()

        self.colors = ["blue", "orange", "purple", "cyan", "magenta"]

    def draw(self):
        self.ax.clear()

        grid = self.sim.environment.grid
        self.ax.imshow(grid, cmap="Greys", origin="lower")

        for i, task in enumerate(self.sim.tasks):
            color = self.colors[i % len(self.colors)]

            if not task.picked_up:
                px, py = task.pickup
                self.ax.scatter(px, py, color=color, s=120, marker="s")
            if not task.delivered:
                dx, dy = task.delivery
                self.ax.scatter(dx, dy, color=color, s=120, marker="x")

        for i, agent in enumerate(self.sim.agents):
            x, y = agent.position
            self.ax.scatter(x, y, color=self.colors[i % len(self.colors)], s=200)

        self.ax.set_title(f"Time: {self.sim.time}")
        self.ax.set_xticks([])
        self.ax.set_yticks([])

    def save_gif(self, filename="simulation.gif", fps=2):

        def update(frame):
            if not self.sim.all_finished():
                self.sim.step()
                self.draw()

        ani = animation.FuncAnimation(
            self.fig,
            update,
            frames=200,
            interval=500,
            repeat=False
        )

        writer = animation.PillowWriter(fps=fps)
        ani.save(filename, writer=writer)

        print(f"Zapisano GIF:", filename)