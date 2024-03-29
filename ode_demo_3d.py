import os
import argparse
from re import I
import time
import numpy as np

import torch
import torch.nn as nn
import torch.optim as optim

parser = argparse.ArgumentParser('ODE demo')
parser.add_argument('--method', type=str, choices=['dopri5', 'adams'], default='dopri5')
parser.add_argument('--data_size', type=int, default=1000)
parser.add_argument('--batch_time', type=int, default=10)
parser.add_argument('--batch_size', type=int, default=20)
parser.add_argument('--niters', type=int, default=2000)
parser.add_argument('--test_freq', type=int, default=20)
parser.add_argument('--viz', action='store_true')
parser.add_argument('--gpu', type=int, default=0)
parser.add_argument('--adjoint', action='store_true')
parser.add_argument('--vecfield', action='store_true', help='displays the learned 3D vector field')
parser.add_argument('--equation', type=str, choices=['spiral', 'expanding_spiral', 'ellipse', 'parabola'], default='spiral', help='specifies the equation that the Neural ODE tries to fit')
parser.add_argument('--start_time', type=int, default=-10, help='specifies the start time for the equation')
parser.add_argument('--end_time', type=int, default=10, help='specifies the end time for the equation')
parser.add_argument('-lr', '--learning_rate', type=float, default=1e-3, help='learning rate for the optimizer')
parser.add_argument('-s', '--network_size', action='count', default=0, help='increase the size of the neural network')
parser.add_argument('--nfull', type=int, default=0, help='the number of full batch iterations after the initial mini-batch training')
parser.add_argument('--momentum', type=float, default=0., help='momentum parameter for the optimizer')
args = parser.parse_args()

if args.adjoint:
    from torchdiffeq import odeint_adjoint as odeint
else:
    from torchdiffeq import odeint

device = torch.device('cuda:' + str(args.gpu) if torch.cuda.is_available() else 'cpu')

def spiral(curr_t):
    return [[torch.sin(torch.pi * curr_t), torch.cos(torch.pi * curr_t), curr_t]]

def expanding_spiral(curr_t):
    return [[curr_t * torch.sin(torch.pi * curr_t) / 10, curr_t * torch.cos(torch.pi * curr_t) / 10, curr_t]]

def ellipse(curr_t):
    return [[torch.cos(torch.pi * curr_t), 2 * torch.sin(torch.pi * curr_t), 3]]

def parabola(curr_t):
    return [[0.2 * curr_t * curr_t + curr_t + 1, 0.3 * curr_t, curr_t]]

equation_func = globals()[args.equation]
t = torch.linspace(args.start_time, args.end_time, args.data_size).to(device)
true_y = torch.tensor(list(map(equation_func, t)))
true_y0 = torch.tensor(equation_func(torch.tensor(args.start_time))).to(device)

def get_full_batch():
    s = torch.from_numpy(np.arange(args.data_size - args.batch_time, dtype=np.int64))
    return get_batch_from_indices(s)


def get_batch():
    s = torch.from_numpy(np.random.choice(np.arange(args.data_size - args.batch_time, dtype=np.int64), args.batch_size, replace=False))
    return get_batch_from_indices(s)

def get_batch_from_indices(s):
    batch_y0 = true_y[s]  # (M, D)
    batch_t = t[:args.batch_time]  # (T)
    batch_y = torch.stack([true_y[s + i] for i in range(args.batch_time)], dim=0)  # (T, M, D)
    return batch_y0.to(device), batch_t.to(device), batch_y.to(device)


def makedirs(dirname):
    if not os.path.exists(dirname):
        os.makedirs(dirname)


if args.viz:
    makedirs('png')
    import matplotlib.pyplot as plt
    from mpl_toolkits import mplot3d
    fig = plt.figure(figsize=(12, 4), facecolor='white')
    ax_traj = fig.add_subplot(131, frameon=False)
    ax_phase = fig.add_subplot(132, projection='3d')
    if (args.vecfield):
        ax_vecfield = fig.add_subplot(133, projection='3d')
    plt.show(block=False)


def visualize(true_y, pred_y, odefunc, itr):

    if args.viz:

        ax_traj.cla()
        ax_traj.set_title('Trajectories')
        ax_traj.set_xlabel('t')
        ax_traj.set_ylabel('x,y,z')
        ax_traj.plot(t.cpu().numpy(), true_y.cpu().numpy()[:, 0, 0], t.cpu().numpy(), true_y.cpu().numpy()[:, 0, 1], t.cpu().numpy(), true_y.cpu().numpy()[:, 0, 2], 'g-')
        ax_traj.plot(t.cpu().numpy(), pred_y.cpu().numpy()[:, 0, 0], '--', t.cpu().numpy(), pred_y.cpu().numpy()[:, 0, 1], 'r--', t.cpu().numpy(), pred_y.cpu().numpy()[:, 0, 2], 'b--')
        ax_traj.set_xlim(t.cpu().min(), t.cpu().max())
        ax_traj.set_ylim(-2, 2)
        ax_traj.legend()

        ax_phase.cla()
        ax_phase.set_title('Phase Portrait')
        ax_phase.set_xlabel('x')
        ax_phase.set_ylabel('y')
        ax_phase.set_zlabel('z')
        ax_phase.plot(true_y.cpu().numpy()[:, 0, 0], true_y.cpu().numpy()[:, 0, 1], true_y.cpu().numpy()[:, 0, 2], 'g-')
        ax_phase.plot(pred_y.cpu().numpy()[:, 0, 0], pred_y.cpu().numpy()[:, 0, 1], pred_y.cpu().numpy()[:, 0, 2], 'b--')
        ax_phase.set_xlim(-2, 2)
        ax_phase.set_ylim(-2, 2)
        ax_phase.set_zlim(args.start_time, args.end_time)

        if (args.vecfield):
            ax_vecfield.cla()
            ax_vecfield.set_title('Learned Vector Field')
            ax_vecfield.set_xlabel('x')
            ax_vecfield.set_ylabel('y')
            ax_vecfield.set_zlabel('z')

            z, y, x = np.mgrid[args.start_time:args.end_time:21j, -2:2:21j, -2:2:21j]
            dydt = odefunc(0, torch.Tensor(np.stack([x, y, z], -1).reshape(21 * 21 * 21, 3)).to(device)).cpu().detach().numpy()
            mag = np.sqrt(dydt[:, 0]**2 + dydt[:, 1]**2 + dydt[:, 2]**2).reshape(-1, 1)
            dydt = (dydt / mag)
            dydt = dydt.reshape(21, 21, 21, 3)

            widths = np.linspace(0, 0.1, x.size)
            ax_vecfield.quiver(x, y, z, dydt[:, :, :, 0], dydt[:, :, :, 1], dydt[:, :, :, 2], linewidths=widths, color="black")
            ax_vecfield.set_xlim(-2, 2)
            ax_vecfield.set_ylim(-2, 2)
            ax_vecfield.set_zlim(args.start_time, args.end_time)

        fig.tight_layout()
        plt.savefig('png/{:03d}'.format(itr))
        plt.draw()
        plt.pause(0.001)


class ODEFunc(nn.Module):

    def __init__(self):
        super(ODEFunc, self).__init__()

        if args.network_size >= 2:
            self.net = nn.Sequential(
                nn.Linear(3, 50),
                nn.Tanh(),
                nn.Linear(50, 150),
                nn.Tanh(),
                nn.Linear(150, 150),
                nn.Tanh(),
                nn.Linear(150, 50),
                nn.Tanh(),
                nn.Linear(50, 3),
            )
        elif args.network_size == 1:
            self.net = nn.Sequential(
                nn.Linear(3, 50),
                nn.Tanh(),
                nn.Linear(50, 150),
                nn.Tanh(),
                nn.Linear(150, 50),
                nn.Tanh(),
                nn.Linear(50, 3),
            )
        else:
            self.net = nn.Sequential(
                nn.Linear(3, 50),
                nn.Tanh(),
                nn.Linear(50, 3),
            )

        for m in self.net.modules():
            if isinstance(m, nn.Linear):
                nn.init.normal_(m.weight, mean=0, std=0.1)
                nn.init.constant_(m.bias, val=0)

    def forward(self, t, y):
        return self.net(y)


class RunningAverageMeter(object):
    """Computes and stores the average and current value"""

    def __init__(self, momentum=0.99):
        self.momentum = momentum
        self.reset()

    def reset(self):
        self.val = None
        self.avg = 0

    def update(self, val):
        if self.val is None:
            self.avg = val
        else:
            self.avg = self.avg * self.momentum + val * (1 - self.momentum)
        self.val = val


if __name__ == '__main__':

    ii = 0

    func = ODEFunc().to(device)
    
    optimizer = optim.RMSprop(func.parameters(), lr=args.learning_rate, momentum=args.momentum)
    end = time.time()

    time_meter = RunningAverageMeter(0.97)
    
    loss_meter = RunningAverageMeter(0.97)

    for itr in range(1, args.niters + args.nfull + 1):
        optimizer.zero_grad()
        if itr > args.niters:
            batch_y0, batch_t, batch_y = get_full_batch()
        else:
            batch_y0, batch_t, batch_y = get_batch()
        pred_y = odeint(func, batch_y0, batch_t).to(device)
        loss = torch.mean(torch.abs(pred_y - batch_y))
        loss.backward()
        optimizer.step()

        time_meter.update(time.time() - end)
        loss_meter.update(loss.item())

        if itr % args.test_freq == 0:
            with torch.no_grad():
                pred_y = odeint(func, true_y0, t)
                loss = torch.mean(torch.abs(pred_y - true_y))
                print('Iter {:04d} | Total Loss {:.6f}'.format(itr, loss.item()))
                visualize(true_y, pred_y, func, ii)
                ii += 1

        end = time.time()
