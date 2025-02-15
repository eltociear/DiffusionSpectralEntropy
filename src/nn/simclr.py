import random
from typing import Tuple

import torch
import torchvision.transforms as transforms
from PIL import ImageFilter
from sklearn.metrics import accuracy_score


class NTXentLoss(torch.nn.Module):

    def __init__(self, temperature: float = 1.0):
        super(NTXentLoss, self).__init__()
        self.temperature = temperature
        self.epsilon = 1e-7

    def forward(self, z1: torch.Tensor, z2: torch.Tensor):
        assert z1.shape == z2.shape
        B, _ = z1.shape

        loss = 0
        z1 = torch.nn.functional.normalize(input=z1, p=2, dim=1)
        z2 = torch.nn.functional.normalize(input=z2, p=2, dim=1)

        # Create a matrix that represent the [i,j] entries of positive pairs.
        # Diagonal (self) are positive pairs.
        pos_pair_ij = torch.diag(torch.ones(B))
        pos_pair_ij = pos_pair_ij.bool()

        # Similarity matrix.
        sim_matrix = torch.matmul(z1, z2.T)

        # Entries noted by 1's in `pos_pair_ij` are similarities of positive pairs.
        numerator = torch.sum(
            torch.exp(sim_matrix[pos_pair_ij] / self.temperature))

        # Entries elsewhere are similarities of negative pairs.
        denominator = torch.sum(
            torch.exp(sim_matrix[~pos_pair_ij] / self.temperature))

        loss += -torch.log(numerator /
                           (denominator + self.epsilon) + self.epsilon)

        pseudo_acc = accuracy_score(
            y_true=pos_pair_ij.cpu().detach().numpy().reshape(-1),
            y_pred=sim_matrix.cpu().detach().numpy().reshape(-1) > 0.5)

        return loss / B, pseudo_acc


class SingleInstanceTwoView:
    '''
    This class is adapted from BarlowTwins and SimSiam in our external_src folder.
    '''

    def __init__(self, imsize: int, mean: Tuple[float], std: Tuple[float]):
        self.augmentation = transforms.Compose([
            transforms.Resize(
                imsize, interpolation=transforms.InterpolationMode.BICUBIC),
            transforms.RandomResizedCrop(
                imsize,
                scale=(0.6, 1.6),
                interpolation=transforms.InterpolationMode.BICUBIC),
            transforms.RandomHorizontalFlip(p=0.5),
            transforms.RandomApply([
                transforms.ColorJitter(
                    brightness=0.8, contrast=0.8, saturation=0.8, hue=0.2)
            ],
                                   p=0.4),
            transforms.RandomGrayscale(p=0.2),
            transforms.RandomApply([GaussianBlur([0.1, 2.0])], p=0.5),
            transforms.ToTensor(),
            transforms.Normalize(mean=mean, std=std)
        ])

    def __call__(self, x):
        aug1 = self.augmentation(x)
        aug2 = self.augmentation(x)
        return aug1, aug2


class GaussianBlur(object):

    def __init__(self, sigma=[0.1, 2.0]):
        self.sigma = sigma

    def __call__(self, x):
        sigma = random.uniform(self.sigma[0], self.sigma[1])
        x = x.filter(ImageFilter.GaussianBlur(radius=sigma))
        return x
