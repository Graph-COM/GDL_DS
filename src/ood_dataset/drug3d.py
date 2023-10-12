import numpy as np
from torch_geometric.data import InMemoryDataset, Data
from pathlib import Path
import torch
from ..utils.url import decide_download, download_url, extract_zip
import os
import mmcv
import rdkit.Chem as Chem
import rdkit.Chem.AllChem as AllChem
import os.path as osp
import shutil


class Drug3d(InMemoryDataset):
    """
    Object for obtaining DrugOOD-3D Dataset (Biochemistry) with its distribution shifts.
        Regarding DrugOOD-3D, in this paper, we utilized three cases of distribution shifts,
        including lbap_core_ic50_assay, lbap_core_ic50_scaffold, lbap_core_ic50_size,
        and we recommend users to find more details in https://github.com/tencent-ailab/DrugOOD.
    """
    def __init__(self, root_dir, data_config, shift_config, seed):
        self.seed = seed
        # TODO: Processed Dataset TO be uploaded.
        self.url_processed = ...
        self.ood_type = shift_config['target']
        ATOM_TYPES = ['C', 'N', 'O', 'S', 'F', 'P', 'Cl', 'Br', 'Na', 'I', 'B', 'H', 'Si', '*']
        self.setting = data_config['setting']
        self.restrict_TL_train = shift_config['restrict_TL_train']
        if self.setting == 'Par-Label':
            self.setting = f'Par-Label_#{self.restrict_TL_train}'

        super().__init__(root_dir)
        self.data, self.slices, self.idx_split = torch.load(self.processed_paths[0])

        self.x_dim = self.data.x.shape[1]
        self.pos_dim = self.data.pos.shape[1]
        self.feature_type = data_config['feature_type']
        self.dataset_name = data_config['data_name']
        node_categorical_feat = [len(ATOM_TYPES)]
        if self.feature_type == 'only_pos':
            node_scalar_feat = self.pos_dim
            node_categorical_feat = []
        elif self.feature_type == 'only_x':
            node_scalar_feat = self.x_dim - 1
        elif self.feature_type == 'only_ones':
            node_scalar_feat = 1
            node_categorical_feat = []
        else:
            assert self.feature_type == 'both_x_pos'
            node_scalar_feat = self.x_dim - 1 + self.pos_dim

        self.feat_info = {'node_categorical_feat': node_categorical_feat, 'node_scalar_feat': node_scalar_feat}

    @property
    def processed_file_names(self):
        return [f"{self.ood_type}_{self.setting}.pt"]

    def download_(self):
        if osp.exists(self.processed_paths[0]):
            return
        print("our raw data are sourced from https://github.com/tencent-ailab/DrugOOD, Download {"
              "lbap_core_ic50_assay, lbap_core_ic50_scaffold, lbap_core_ic50_size}.json to the raw_dir "
              "../dataset/DrugOOD-3D/raw/ if you need. (not necessary!)")
        print("Processed Dataset TO be uploaded. The URL could not be used now.")
        exit(-1)
        if decide_download(self.url_processed, is_raw=False):
            path = download_url(self.url_processed, self.root)
            extract_zip(path, self.root)
            os.unlink(path)
        else:
            print('Stop downloading.')
            shutil.rmtree(self.root)
            exit(-1)

    def process(self):
        self.download_()
        base_dir = Path(self.root) / 'raw'
        dataset = mmcv.load(base_dir / f"{self.ood_type}.json")["split"]
        idx_split, Dataset = dict(), []
        if self.setting.split('_')[0] == 'Par-Label':
            np.random.seed(42)
            # 8k samples for evaluation
            ood_val_idx = np.arange(len(dataset['ood_val']))
            for_train_v = np.random.choice(ood_val_idx, size=1000, replace=False)
            val_index = np.setdiff1d(ood_val_idx, for_train_v)
            ood_test_idx = np.arange(len(dataset['ood_test']))
            for_train_t = np.random.choice(ood_test_idx, size=1000, replace=False)
            test_index = np.setdiff1d(ood_test_idx, for_train_t)
            assert len(val_index) + len(for_train_v) == len(dataset['ood_val'])
            val_set, test_set, train_v, train_t = [], [], [], []
            for idx in val_index:
                build = build_drug_object(dataset["ood_val"][idx])
                if build is None:
                    continue
                val_set.append(build)
            for idx in test_index:
                build = build_drug_object(dataset["ood_test"][idx])
                if build is None:
                    continue
                test_set.append(build)
            for idx in for_train_v:
                build = build_drug_object(dataset["ood_val"][idx])
                if build is None:
                    continue
                train_v.append(build)
            for idx in for_train_t:
                build = build_drug_object(dataset["ood_test"][idx])
                if build is None:
                    continue
                train_t.append(build)

            for_train_v_ = np.random.choice(range(500), int(self.restrict_TL_train / 2), replace=False)
            train_v_sample = [train_v[i] for i in for_train_v_]
            train_t_sample = [train_t[i] for i in for_train_v_]
            dataset_dict = {'train': train_v_sample + train_t_sample, 'val': val_set, 'test': test_set}
            dataset_dict_ = dataset_dict
        else:
            dataset_dict_ = dict()
            dataset_dict = {'train': [], 'iid_val': [], 'iid_test': [], 'ood_val': [], 'ood_test': []}
            for item in dataset_dict.keys():
                subset = dataset[item]
                for idx, data in enumerate(subset):
                    build = build_drug_object(data)
                    if build is None:
                        print(f'Failed to embed molecule {idx} in {item} dataset.')
                        continue
                    dataset_dict[item].append(build)
            if self.setting.split('_')[0] == 'No-Info':
                dataset_dict_ = dataset_dict
            elif self.setting.split('_')[0] == 'O-Feature':
                dataset_dict_ = {'train_source': dataset_dict['train'],
                                 'train_target': dataset_dict['ood_val']+dataset_dict['ood_test'],
                                 'iid_val': dataset_dict['iid_val'],
                                 'iid_test': dataset_dict['iid_test'],
                                 'ood_val': dataset_dict['ood_val'],
                                 'ood_test': dataset_dict['ood_test']}

        for item in dataset_dict_.keys():
            idx_split[item] = [i + len(Dataset) for i in range(len(dataset_dict_[item]))]
            Dataset += dataset_dict_[item]

        data, slices = self.collate(Dataset)
        torch.save((data, slices, idx_split), self.processed_paths[0])


def build_drug_object(data):
    ATOM_TYPES_ = ['C', 'N', 'O', 'S', 'F', 'P', 'Cl', 'Br', 'Na', 'I', 'B', 'H', 'Si']

    def match_element_index(element):
        return ATOM_TYPES_.index(element) if element in ATOM_TYPES_ else len(ATOM_TYPES_)

    reg_label, y, assay_id, domain_id = data['reg_label'], data['cls_label'], data['assay_id'], data[
        'domain_id']
    y = torch.tensor(y).float().view(-1, 1)
    smiles = data['smiles']
    mol = Chem.MolFromSmiles(smiles)
    m = Chem.AddHs(mol)
    message_id = AllChem.EmbedMolecule(m, randomSeed=0)
    if message_id < 0:
        return None
    message_id_ = AllChem.MMFFOptimizeMolecule(m, maxIters=1000)
    if message_id_ < 0:
        return None
    m = Chem.RemoveHs(m)
    pos = torch.tensor(m.GetConformer().GetPositions(), dtype=torch.float)
    # the index order of `pos` corresponds to that of `m.GetAtoms()`
    x = torch.tensor([match_element_index(atom.GetSymbol()) for atom in m.GetAtoms()]).unsqueeze(1)
    assert x.shape[0] == m.GetNumAtoms()
    for j in range(m.GetNumAtoms()):
        assert ATOM_TYPES_[x[j]] == m.GetAtomWithIdx(j).GetSymbol() or m.GetAtomWithIdx(
            j).GetSymbol() not in ATOM_TYPES_

    return Data(x=x, pos=pos, y=y, reg_label=reg_label, assay_id=assay_id, domain_id=domain_id)
