import torch
import torch.nn as nn
from torch_geometric.nn import BatchNorm


class MLP(nn.Sequential):
    def __init__(self, channels, dropout_p, norm_type, act_type):
        norm = self.get_norm(norm_type)
        act = self.get_act(act_type)

        m = []
        for i in range(1, len(channels)):
            m.append(nn.Linear(channels[i - 1], channels[i]))

            if i < len(channels) - 1:
                m.append(norm(channels[i]))
                m.append(act())
                m.append(nn.Dropout(dropout_p))

        super(MLP, self).__init__(*m)

    @staticmethod
    def get_norm(norm_type):
        if isinstance(norm_type, str) and 'batch' in norm_type:
            return BatchNorm
        elif norm_type == 'none' or norm_type is None:
            return nn.Identity
        else:
            raise ValueError('Invalid normalization type: {}'.format(norm_type))

    @staticmethod
    def get_act(act_type):
        if act_type == 'relu':
            return nn.ReLU
        elif act_type == 'silu':
            return nn.SiLU
        elif act_type == 'none':
            return nn.Identity
        else:
            raise ValueError('Invalid activation type: {}'.format(act_type))


class CoorsNorm(nn.Module):
    def __init__(self, eps=1e-6, scale_init=1.):
        super().__init__()
        self.eps = eps
        scale = torch.zeros(1).fill_(scale_init)
        self.scale = nn.Parameter(scale)

    def forward(self, coors):
        norm = coors.norm(dim=-1, keepdim=True)
        normed_coors = coors / norm.clamp(min=self.eps)
        return normed_coors  # * self.scale


class ExtractorMLP(nn.Module):

    def __init__(self, hidden_size, config):
        super().__init__()
        dropout_p = config['dropout_p']
        norm_type = config['norm_type']
        act_type = config['act_type']
        self.feature_extractor = MLP([hidden_size * 1, hidden_size * 2, hidden_size, 1], dropout_p, norm_type,
                                     act_type)

    def forward(self, emb):
        logits = self.feature_extractor(emb)
        return logits


class domain_disc(nn.Module):

    def __init__(self, hidden_size, config):
        super().__init__()
        dropout_p = config['dropout_p']
        norm_type = config['norm_type']
        act_type = config['act_type']
        hidden_size_ = 256
        self.feature_extractor = MLP([hidden_size, hidden_size_, hidden_size_, 1], dropout_p, norm_type,
                                     act_type)

    def forward(self, emb):
        logits = self.feature_extractor(emb)
        return logits


class FeatEncoder(torch.nn.Module):

    def __init__(self, hidden_size, categorical_feat, scalar_feat, n_categorical_feat_to_use=-1,
                 n_scalar_feat_to_use=-1):
        super().__init__()
        self.embedding_list = torch.nn.ModuleList()

        self.num_categorical_feat = len(categorical_feat)
        self.n_categorical_feat_to_use = self.num_categorical_feat if n_categorical_feat_to_use == -1 else n_categorical_feat_to_use
        self.num_scalar_feat_to_use = scalar_feat if n_scalar_feat_to_use == -1 else n_scalar_feat_to_use

        for i in range(self.n_categorical_feat_to_use):
            num_categories = categorical_feat[i]
            emb = torch.nn.Embedding(num_categories, hidden_size)
            self.embedding_list.append(emb)

        if self.num_scalar_feat_to_use > 0:
            assert n_scalar_feat_to_use == -1
            self.linear = torch.nn.Linear(self.num_scalar_feat_to_use, hidden_size)

        total_cate_dim = self.n_categorical_feat_to_use * hidden_size
        self.dim_mapping = torch.nn.Linear(total_cate_dim + hidden_size,
                                           hidden_size) if self.num_scalar_feat_to_use > 0 else torch.nn.Linear(
            total_cate_dim, hidden_size)

    def forward(self, x):
        x_embedding = []
        for i in range(self.n_categorical_feat_to_use):
            x_embedding.append(self.embedding_list[i](x[:, i].long()))

        if self.num_scalar_feat_to_use > 0:
            x_embedding.append(self.linear(x[:, self.num_categorical_feat:]))

        x_embedding = self.dim_mapping(torch.cat(x_embedding, dim=-1))
        return x_embedding


def get_optimizer(clf, extractor, optimizer_config):
    pred_lr = optimizer_config['wp_lr']
    pred_wd = optimizer_config['wp_wd']

    attn_lr = optimizer_config['attn_lr']
    attn_wd = optimizer_config['attn_wd']
    emb_lr = optimizer_config['emb_lr']
    emb_wd = optimizer_config['emb_wd']

    algo = torch.optim.Adam
    clf_emb_model_params = [kv[1] for kv in clf.named_parameters() if 'emb_model' in kv[0]]
    clf_base_params = [kv[1] for kv in clf.named_parameters() if 'emb_model' not in kv[0]]

    return algo(
        [{'params': extractor.parameters(), 'lr': attn_lr, 'weight_decay': attn_wd}, {'params': clf_base_params},
         {'params': clf_emb_model_params, 'lr': emb_lr, 'weight_decay': emb_wd}], lr=pred_lr, weight_decay=pred_wd)


def get_wp_optimizer(clf, optimizer_config):
    wp_lr = optimizer_config['wp_lr']
    wp_wd = optimizer_config['wp_wd']
    clf_base_params = [kv[1] for kv in clf.named_parameters() if 'emb_model' not in kv[0]]
    return torch.optim.Adam([{'params': clf_base_params}], lr=wp_lr, weight_decay=wp_wd)


def get_dir_optimizer(clf, extractor, optimizer_config, method_config):
    # for predict, including prediction for y_s and y_c
    pred_lr = optimizer_config['wp_lr']
    pred_wd = optimizer_config['wp_wd']

    conf_optimizer = torch.optim.Adam(clf.mlp_out_conf.parameters(), lr=pred_lr, weight_decay=pred_wd)
    model_optimizer = torch.optim.Adam(
        list(extractor.parameters()) +
        list(clf.parameters()),
        lr=pred_lr, weight_decay=pred_wd
    )
    return conf_optimizer, model_optimizer


def get_dann_optimizer(clf, disc, optimizer_config):
    wp_lr = optimizer_config['wp_lr']
    wp_wd = optimizer_config['wp_wd']
    return torch.optim.Adam(list(clf.parameters()) + list(disc.parameters()),
                            lr=wp_lr, weight_decay=wp_wd)
