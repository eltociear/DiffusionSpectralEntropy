import argparse
import os
import sys
from typing import Tuple, Dict, Iterable
from matplotlib import pyplot as plt
from scipy.stats import pearsonr, spearmanr
import pandas as pd

import numpy as np
import torch
import torchvision
from tqdm import tqdm
import timm

import_dir = '/'.join(os.path.realpath(__file__).split('/')[:-4])
sys.path.insert(0, import_dir + '/api/')
from dse import diffusion_spectral_entropy
from dsmi import diffusion_spectral_mutual_information

sys.path.insert(0, import_dir + '/src/utils/')
from attribute_hashmap import AttributeHashmap
from seed import seed_everything
from extend import ExtendedDataset


class ImageNetSubset(torch.utils.data.Dataset):

    def __init__(self, full_dataset: torch.utils.data.Dataset):
        self.dataset = full_dataset

        assert 'targets' in full_dataset.__dir__()
        self.dataset.labels = full_dataset.targets

        assert 'imgs' in full_dataset.__dir__()
        self.dataset.imgs = full_dataset.imgs

        # Imagenette
        # tench, English springer, cassette player, chain saw, church,
        # French horn, garbage truck, gas pump, golf ball, parachute.
        imagenette_label_idx = [0, 217, 482, 491, 497, 566, 569, 571, 574, 701]

        # Imagewoof
        # Australian terrier, Border terrier, Samoyed, Beagle, Shih-Tzu,
        # English foxhound, Rhodesian ridgeback, Dingo, Golden retriever, Old English sheepdog.
        imagewoof_label_idx = [
            193, 182, 258, 162, 155, 167, 159, 273, 207, 229
        ]

        label_indices = imagenette_label_idx + imagewoof_label_idx

        self.indices = [y in label_indices for y in self.dataset.labels]
        self.indices = np.argwhere(self.indices).reshape(-1)

    def __getitem__(self, idx):
        if isinstance(idx, list):
            return self.dataset[[self.indices[i] for i in idx]]
        return self.dataset[self.indices[idx]]

    def __len__(self):
        return len(self.indices)


# class TopKSubset(torch.utils.data.Dataset):

#     def __init__(self, full_dataset: torch.utils.data.Dataset, topK: int = 20):
#         self.dataset = full_dataset

#         if 'targets' in full_dataset.__dir__():
#             # `targets` used in MNIST, CIFAR10, CIFAR100, ImageNet
#             self.dataset.labels = full_dataset.targets
#         elif 'labels' in full_dataset.__dir__():
#             # `labels` used in STL10
#             self.dataset.labels = full_dataset.labels
#         else:
#             raise NotImplementedError(
#                 '`TopKSubset`: check the `label` str in dataset and update me.'
#             )

#         if 'imgs' in full_dataset.__dir__():
#             self.dataset.imgs = full_dataset.imgs
#         else:
#             raise NotImplementedError(
#                 '`TopKSubset`: check the `image` str in dataset and update me.'
#             )

#         # Find the topK frequent labels.
#         _, counts = np.unique(self.dataset.labels, return_counts=True)
#         # `mergesort` is `stable` such that it gives the same tie-breaking consistently.
#         topK_label_idx = np.argsort(-counts, kind='mergesort')[:topK]
#         self.indices = [y in topK_label_idx for y in self.dataset.labels]
#         self.indices = np.argwhere(self.indices).reshape(-1)

#     def __getitem__(self, idx):
#         if isinstance(idx, list):
#             return self.dataset[[self.indices[i] for i in idx]]
#         return self.dataset[self.indices[idx]]

#     def __len__(self):
#         return len(self.indices)


def get_val_loader(
    args: AttributeHashmap
) -> Tuple[Tuple[
        torch.utils.data.DataLoader,
], AttributeHashmap]:
    try:
        assert args.dataset == 'imagenet'
    except:
        raise ValueError(
            '`args.dataset` value not supported. Value provided: %s.' %
            args.dataset)

    dataset_mean = (0.485, 0.456, 0.406)
    dataset_std = (0.229, 0.224, 0.225)
    torchvision_dataset = torchvision.datasets.ImageNet

    # Validation set has too few images per class. Bad for DSE and DSMI estimation.
    # Therefore we augment and extend it by a bit.
    transform_val = torchvision.transforms.Compose([
        torchvision.transforms.Resize(
            args.imsize,
            interpolation=torchvision.transforms.InterpolationMode.BICUBIC),
        torchvision.transforms.RandomResizedCrop(
            args.imsize,
            scale=(0.6, 1.6),
            interpolation=torchvision.transforms.InterpolationMode.BICUBIC),
        torchvision.transforms.ToTensor(),
        torchvision.transforms.Normalize(mean=dataset_mean, std=dataset_std)
    ])

    val_dataset = torchvision_dataset(args.dataset_dir,
                                      split='val',
                                      transform=transform_val)

    val_dataset = ImageNetSubset(full_dataset=val_dataset)

    val_dataset = ExtendedDataset(val_dataset,
                                  desired_len=3 * len(val_dataset))

    val_loader = torch.utils.data.DataLoader(val_dataset,
                                             batch_size=args.batch_size,
                                             num_workers=args.num_workers,
                                             shuffle=False,
                                             pin_memory=True)

    return val_loader


class ThisArchitectureIsWeirdError(Exception):
    pass


def plot_figures(data_arrays: Dict[str, Iterable], save_path_fig: str) -> None:
    plt.rcParams['font.family'] = 'serif'
    plt.rcParams['legend.fontsize'] = 20

    # Plot of DSE vs val top 1 acc.
    fig = plt.figure(figsize=(40, 20))
    img_idx = 1
    for y_str in [
            'imagenet_val_acc_top1', 'imagenet_val_acc_top5',
            'imagenet_test_acc_top1', 'imagenet_test_acc_top5'
    ]:
        for x_str in [
                'dse_Z', 'dsmi_Z_X', 'dsmi_Z_Y', 'cse_Z', 'csmi_Z_X',
                'csmi_Z_Y'
        ]:
            ax = fig.add_subplot(4, 6, img_idx)
            img_idx += 1
            plot_subplot(ax, data_arrays, x_str, y_str)
    fig.tight_layout()
    fig.savefig(save_path_fig)
    plt.close(fig=fig)
    return


def plot_subplot(ax: plt.Axes, data_arrays: Dict[str, Iterable], x_str: str,
                 y_str: str) -> plt.Axes:
    arr_title_map = {
        'imagenet_val_acc_top1': 'ImageNet val top1-acc',
        'imagenet_val_acc_top5': 'ImageNet val top5-acc',
        'imagenet_test_acc_top1': 'ImageNet test top1-acc',
        'imagenet_test_acc_top5': 'ImageNet test top5-acc',
        'dse_Z': 'DSE ' + r'$S_D(Z)$',
        'cse_Z': 'CSE ' + r'$H(Z)$',
        'dsmi_Z_Y': 'DSMI ' + r'$I_D(Z; Y)$',
        'dsmi_Z_X': 'DSMI ' + r'$I_D(Z; X)$',
        'csmi_Z_Y': 'CSMI ' + r'$I(Z; Y)$',
        'csmi_Z_X': 'CSMI ' + r'$I(Z; X)$',
    }
    ax.spines[['right', 'top']].set_visible(False)
    ax.scatter(data_arrays[x_str],
               data_arrays[y_str],
               c='forestgreen',
               alpha=0.5,
               s=np.array(data_arrays['model_params']) / 2)
    ax.set_xlabel(arr_title_map[x_str], fontsize=20)
    ax.set_ylabel(arr_title_map[y_str], fontsize=20)
    if len(data_arrays[x_str]) > 1:
        ax.set_title('P.R: %.3f (p = %.3f), S.R: %.3f (p = %.3f)' % \
                    (pearsonr(data_arrays[x_str], data_arrays[y_str])[0],
                    pearsonr(data_arrays[x_str], data_arrays[y_str])[1],
                    spearmanr(data_arrays[x_str], data_arrays[y_str])[0],
                    spearmanr(data_arrays[x_str], data_arrays[y_str])[1]),
                    fontsize=16)
    ax.tick_params(axis='both', which='major', labelsize=20)
    return


class ModelWithLatentAccess(torch.nn.Module):

    def __init__(self,
                 timm_model: torch.nn.Module,
                 num_classes: int = 10) -> None:
        super(ModelWithLatentAccess, self).__init__()
        self.num_classes = num_classes

        # Isolate the model into an encoder and a linear classifier.
        self.encoder = timm_model

        # Get the correct dimensions of the last linear layer and remove the linear layer.
        # The last linear layer may come with different names...
        if any(n == 'fc' for (n, _) in self.encoder.named_children()) and \
           isinstance(self.encoder.fc, torch.nn.modules.Linear):
            last_layer = self.encoder.fc
            last_layer_name_opt = 1
        elif any(n == 'classifier' for (n, _) in self.encoder.named_children()) and \
           isinstance(self.encoder.classifier, torch.nn.modules.Linear):
            last_layer = self.encoder.classifier
            last_layer_name_opt = 2
        elif any(n == 'head' for (n, _) in self.encoder.named_children()) and \
           isinstance(self.encoder.head, torch.nn.modules.Linear):
            last_layer = self.encoder.head
            last_layer_name_opt = 3
        elif any(n == 'head' for (n, _) in self.encoder.named_children()) and \
             any(n == 'fc' for (n, _) in self.encoder.head.named_children()) and \
           isinstance(self.encoder.head.fc, torch.nn.modules.Linear):
            last_layer = self.encoder.head.fc
            last_layer_name_opt = 4
        elif any(n == 'head' for (n, _) in self.encoder.named_children()) and \
             any(n == 'fc' for (n, _) in self.encoder.head.named_children()) and \
             any(n == 'fc2' for (n, _) in self.encoder.head.fc.named_children()) and \
           isinstance(self.encoder.head.fc.fc2, torch.nn.modules.Linear):
            last_layer = self.encoder.head.fc.fc2
            last_layer_name_opt = 5
        else:
            raise ThisArchitectureIsWeirdError

        assert last_layer.out_features == num_classes

        self.linear = last_layer

        if last_layer_name_opt == 1:
            self.encoder.fc = torch.nn.Identity()
        elif last_layer_name_opt == 2:
            self.encoder.classifier = torch.nn.Identity()
        elif last_layer_name_opt == 3:
            self.encoder.head = torch.nn.Identity()
        elif last_layer_name_opt == 4:
            self.encoder.head.fc = torch.nn.Identity()
        elif last_layer_name_opt == 5:
            self.encoder.head.fc.fc2 = torch.nn.Identity()

    def encode(self, x):
        return self.encoder(x)


def main(args: AttributeHashmap) -> None:
    '''
    Compute DSE and DSMI, and compute correlation with ImageNet acc.
    '''
    save_path_numpy = './results.npz'
    save_path_fig = './results'

    in_channels_map = {
        'imagenet': 3,
    }
    num_classes_map = {
        'imagenet': 1000,
    }
    dataset_dir_map = {
        'imagenet':
        '/gpfs/gibbs/pi/krishnaswamy_smita/cl2482/DiffusionSpectralEntropy/data/imagenet',
    }
    args.in_channels = in_channels_map[args.dataset]
    args.num_classes = num_classes_map[args.dataset]
    args.dataset_dir = dataset_dir_map[args.dataset]

    # Load the tables for ImageNet accuracy (val and test set).
    df_val = pd.read_csv('./results-imagenet.csv')
    df_test = pd.read_csv('./results-imagenet-real.csv')

    df_val.drop([
        'top1_err', 'top5_err', 'crop_pct', 'interpolation', 'img_size',
        'param_count'
    ],
                axis=1,
                inplace=True)
    df_test.drop([
        'top1_err', 'top5_err', 'crop_pct', 'interpolation', 'top1_diff',
        'top5_diff', 'rank_diff'
    ],
                 axis=1,
                 inplace=True)
    df_val.rename(columns={
        'top1': 'val_acc_top1',
        'top5': 'val_acc_top5'
    },
                  inplace=True)
    df_test.rename(columns={
        'top1': 'test_acc_top1',
        'top5': 'test_acc_top5'
    },
                   inplace=True)
    df_combined = df_val.merge(df_test, on='model')
    del df_val, df_test

    # Iterate over all models and evaluate results.
    if os.path.isfile(save_path_numpy) and not args.restart:
        npz_file = np.load(save_path_numpy)
        results_dict = {
            'model_names': list(npz_file['model_names']),
            'model_params': list(npz_file['model_params']),
            'imagenet_val_acc_top1': list(npz_file['imagenet_val_acc_top1']),
            'imagenet_val_acc_top5': list(npz_file['imagenet_val_acc_top5']),
            'imagenet_test_acc_top1': list(npz_file['imagenet_test_acc_top1']),
            'imagenet_test_acc_top5': list(npz_file['imagenet_test_acc_top5']),
            'dse_Z': list(npz_file['dse_Z']),
            'cse_Z': list(npz_file['cse_Z']),
            'dsmi_Z_X': list(npz_file['dsmi_Z_X']),
            'csmi_Z_X': list(npz_file['csmi_Z_X']),
            'dsmi_Z_Y': list(npz_file['dsmi_Z_Y']),
            'csmi_Z_Y': list(npz_file['csmi_Z_Y']),
        }

    else:
        results_dict = {
            'model_names': [],
            'model_params': [],
            'imagenet_val_acc_top1': [],
            'imagenet_val_acc_top5': [],
            'imagenet_test_acc_top1': [],
            'imagenet_test_acc_top5': [],
            'dse_Z': [],
            'cse_Z': [],
            'dsmi_Z_X': [],
            'csmi_Z_X': [],
            'dsmi_Z_Y': [],
            'csmi_Z_Y': [],
        }

    # Iterate over the model candidates with pretrained weights.
    for _i, model_candidate in tqdm(df_combined.iterrows(),
                                    total=len(df_combined)):

        # This is for resuming progress.
        if model_candidate['model'] in results_dict['model_names']:
            print('Model already evaluated: %s' % model_candidate['model'])
            continue

        args.imsize = model_candidate['img_size']

        try:
            device = torch.device(
                'cuda:%d' %
                args.gpu_id if torch.cuda.is_available() else 'cpu')
            model = timm.create_model(model_name=model_candidate['model'],
                                      num_classes=args.num_classes,
                                      pretrained=True).to(device)
        except RuntimeError as e:
            if not "CUDA out of memory. " in str(e):
                print(
                    'When evaluating %s, hit error %s. Skipping this model.' %
                    (model_candidate['model'], e))
                continue
            device = torch.device('cpu')
            model = timm.create_model(model_name=model_candidate['model'],
                                      num_classes=args.num_classes,
                                      pretrained=True).to(device)

        try:
            model = ModelWithLatentAccess(model, num_classes=args.num_classes)
        except ThisArchitectureIsWeirdError as _:
            print('Cannot process: %s. Skipping it.' %
                  model_candidate['model'])
            continue

        model.eval()
        val_loader = get_val_loader(args=args)

        try:
            dse_Z, cse_Z, dsmi_Z_X, csmi_Z_X, dsmi_Z_Y, csmi_Z_Y = evaluate_dse_dsmi(
                args=args, val_loader=val_loader, model=model, device=device)
        except RuntimeError as e:
            if not "CUDA out of memory. " in str(e):
                print(
                    'When evaluating %s, hit error %s. Skipping this model.' %
                    (model_candidate['model'], e))
                continue
            device = torch.device('cpu')
            model = timm.create_model(model_name=model_candidate['model'],
                                      num_classes=args.num_classes,
                                      pretrained=True).to(device)
            model = ModelWithLatentAccess(model, num_classes=args.num_classes)
            dse_Z, cse_Z, dsmi_Z_X, csmi_Z_X, dsmi_Z_Y, csmi_Z_Y = evaluate_dse_dsmi(
                args=args, val_loader=val_loader, model=model, device=device)

        results_dict['model_names'].append(model_candidate['model'])
        results_dict['model_params'].append(
            float(model_candidate['param_count'].replace(',', '')))
        results_dict['imagenet_val_acc_top1'].append(
            model_candidate['val_acc_top1'])
        results_dict['imagenet_val_acc_top5'].append(
            model_candidate['val_acc_top5'])
        results_dict['imagenet_test_acc_top1'].append(
            model_candidate['test_acc_top1'])
        results_dict['imagenet_test_acc_top5'].append(
            model_candidate['test_acc_top5'])
        results_dict['dse_Z'].append(dse_Z)
        results_dict['cse_Z'].append(cse_Z)
        results_dict['dsmi_Z_X'].append(dsmi_Z_X)
        results_dict['csmi_Z_X'].append(csmi_Z_X)
        results_dict['dsmi_Z_Y'].append(dsmi_Z_Y)
        results_dict['csmi_Z_Y'].append(csmi_Z_Y)

        # Delete model from cache.
        os.system('rm -rf /home/cl2482/.cache/huggingface/hub/')

        # It takes a long time to evaluate all models.
        # Plot and save results after each model evaluation.
        plot_figures(results_dict, save_path_fig=save_path_fig)
        with open(save_path_numpy, 'wb+') as f:
            np.savez(
                f,
                model_names=np.array(results_dict['model_names']),
                model_params=np.array(results_dict['model_params']),
                imagenet_val_acc_top1=np.array(
                    results_dict['imagenet_val_acc_top1']),
                imagenet_val_acc_top5=np.array(
                    results_dict['imagenet_val_acc_top5']),
                imagenet_test_acc_top1=np.array(
                    results_dict['imagenet_test_acc_top1']),
                imagenet_test_acc_top5=np.array(
                    results_dict['imagenet_test_acc_top5']),
                dse_Z=np.array(results_dict['dse_Z']),
                cse_Z=np.array(results_dict['cse_Z']),
                dsmi_Z_X=np.array(results_dict['dsmi_Z_X']),
                csmi_Z_X=np.array(results_dict['csmi_Z_X']),
                dsmi_Z_Y=np.array(results_dict['dsmi_Z_Y']),
                csmi_Z_Y=np.array(results_dict['csmi_Z_Y']),
            )

    return


@torch.no_grad()
def evaluate_dse_dsmi(args: AttributeHashmap,
                      val_loader: torch.utils.data.DataLoader,
                      model: torch.nn.Module, device: torch.device):

    tensor_X = None  # input
    tensor_Y = None  # label
    tensor_Z = None  # latent

    model.eval()
    for x, y_true in tqdm(val_loader):
        assert args.in_channels in [1, 3]
        if args.in_channels == 1:
            # Repeat the channel dimension: 1 channel -> 3 channels.
            x = x.repeat(1, 3, 1, 1)
        x, y_true = x.to(device), y_true.to(device)

        ## Record data for DSE and DSMI computation.

        # Downsample the input image to reduce memory usage.
        curr_X = torch.nn.functional.interpolate(
            x, size=(64, 64)).cpu().numpy().reshape(x.shape[0], -1)
        curr_Y = y_true.cpu().numpy()
        curr_Z = model.encode(x).cpu().numpy()
        if tensor_X is None:
            tensor_X, tensor_Y, tensor_Z = curr_X, curr_Y, curr_Z
        else:
            tensor_X = np.vstack((tensor_X, curr_X))
            tensor_Y = np.hstack((tensor_Y, curr_Y))
            tensor_Z = np.vstack((tensor_Z, curr_Z))

    # For DSE, subsample for faster computation.
    dse_Z = diffusion_spectral_entropy(embedding_vectors=tensor_Z)
    cse_Z = diffusion_spectral_entropy(embedding_vectors=tensor_Z,
                                       classic_shannon_entropy=True)
    dsmi_Z_X, _ = diffusion_spectral_mutual_information(
        embedding_vectors=tensor_Z, reference_vectors=tensor_X,
        n_clusters=10)  # Imagenette
    csmi_Z_X, _ = diffusion_spectral_mutual_information(
        embedding_vectors=tensor_Z,
        reference_vectors=tensor_X,
        n_clusters=10,  # Imagenette
        classic_shannon_entropy=True)

    dsmi_Z_Y, _ = diffusion_spectral_mutual_information(
        embedding_vectors=tensor_Z, reference_vectors=tensor_Y)
    csmi_Z_Y, _ = diffusion_spectral_mutual_information(
        embedding_vectors=tensor_Z,
        reference_vectors=tensor_Y,
        classic_shannon_entropy=True)

    return dse_Z, cse_Z, dsmi_Z_X, csmi_Z_X, dsmi_Z_Y, csmi_Z_Y


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--gpu-id',
                        help='Available GPU index.',
                        type=int,
                        default=0)
    parser.add_argument('--random-seed', type=int, default=1)
    parser.add_argument('--dataset', type=str, default='imagenet')
    parser.add_argument('--batch-size', type=int, default=16)
    parser.add_argument('--num-workers', type=int, default=8)
    parser.add_argument(
        '--restart',
        action='store_true',
        help=
        'If turned on, recompute from the first model. Otherwise resume where it left off.'
    )
    args = vars(parser.parse_args())

    args = AttributeHashmap(args)
    seed_everything(args.random_seed)
    main(args)
