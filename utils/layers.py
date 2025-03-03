# encoding=utf-8

import torch.nn.functional as F
from utils.utils import *
from torch.nn import Parameter

try:
    from mish_cuda import MishCuda as Mish
except:
    class Mish(nn.Module):  # https://github.com/digantamisra98/Mish
        def forward(self, x):
            return x * F.softplus(x).tanh()


# Arc loss
class ArcMargin(nn.Module):
    r"""
    Implement of large margin arc distance: :
        Args:
            in_features: size of each input sample
            out_features: size of each output sample
            s: norm of input feature
            m: margin

            cos(theta + m)
        """

    def __init__(self,
                 in_features,
                 out_features,
                 device,
                 s=30.0,
                 m=0.50,
                 easy_margin=False):
        """
        ArcMargin
        :type in_features: int
        :type out_features: int
        :param in_features:
        :param out_features:
        :param s:
        :param m:
        :param easy_margin:
        """
        super(ArcMargin, self).__init__()

        self.device = device
        self.in_dim = in_features
        self.out_dim = out_features
        print('=> in dim: %d, out dim: %d' % (self.in_dim, self.out_dim))

        self.s = s
        self.m = m

        # 根据输入输出dim确定初始化权重
        self.weight = Parameter(torch.FloatTensor(self.out_dim, self.in_dim))
        nn.init.xavier_uniform_(self.weight)

        self.easy_margin = easy_margin
        self.cos_m = math.cos(m)
        self.sin_m = math.sin(m)
        self.th = math.cos(math.pi - m)
        self.mm = math.sin(math.pi - m) * m

    def forward(self, input, label):
        # --------------------------- cos(theta) & phi(theta) ---------------------------
        # L2 normalize and calculate cosine
        cosine = F.linear(F.normalize(input, p=2), F.normalize(self.weight, p=2))

        sine = torch.sqrt(1.0 - torch.pow(cosine, 2))

        # phi: cos(θ+m)
        phi = cosine * self.cos_m - sine * self.sin_m
        if self.easy_margin:
            phi = torch.where(cosine > 0, phi, cosine)
        else:
            phi = torch.where(cosine > self.th, phi, cosine - self.mm)

        # --------------------------- convert label to one-hot ---------------------------
        # one_hot = torch.zeros(cosine.size(), requires_grad=True, device='cuda')
        one_hot = torch.zeros(cosine.size(), device=self.device)  # device='cuda'
        one_hot.scatter_(1, label.view(-1, 1).long(), 1)

        # -------------torch.where(out_i = {x_i if condition_i else y_i) -------------
        # you can use torch.where if your torch.__version__ >= 0.4
        try:
            output = (one_hot * phi) + ((1.0 - one_hot) * cosine)
            output *= self.s
            # print(output)
        except Exception as e:
            print(e)

        return output


def make_divisible(v, divisor):
    # Function ensures all layers have a channel number that is divisible by 8
    # https://github.com/tensorflow/models/blob/master/research/slim/nets/mobilenet/mobilenet.py
    return math.ceil(v / divisor) * divisor


class Flatten(nn.Module):
    # Use after nn.AdaptiveAvgPool2d(1) to remove last 2 dimensions
    def forward(self, x):
        return x.view(x.size(0), -1)


class Concat(nn.Module):
    # Concatenate a list of tensors along dimension
    def __init__(self, dimension=1):
        super(Concat, self).__init__()
        self.d = dimension

    def forward(self, x):
        return torch.cat(x, self.d)


class RouteGroup(nn.Module):
    def __init__(self, layers, groups, group_id):
        """
        :param layers:
        :param groups:
        :param group_id:
        """
        super(RouteGroup, self).__init__()
        self.layers = layers
        self.multi = len(layers) > 1
        self.groups = groups
        self.group_id = group_id

    def forward(self, x, outputs):
        """
        :param x:
        :param outputs:
        :return:
        """
        if self.multi:
            outs = []
            for layer in self.layers:
                out = torch.chunk(outputs[layer], self.groups, dim=1)
                outs.append(out[self.group_id])
            return torch.cat(outs, dim=1)
        else:
            out = torch.chunk(outputs[self.layers[0]], self.groups, dim=1)
            return out[self.group_id]


# SAM layer: ScaleSpatial
class SAM(nn.Module):  # weighted sum of 2 or more layers https://arxiv.org/abs/1911.09070
    def __init__(self, layers):
        super(SAM, self).__init__()
        self.layers = layers  # layer indices

    def forward(self, x, outputs):  # using x as point-wise spacial attention[0, 1]
        a = outputs[self.layers[0]]  # using a as input feature
        return x * a  # point-wise multiplication


class ScaleChannel(nn.Module):  # weighted sum of 2 or more layers https://arxiv.org/abs/1911.09070
    def __init__(self, layers):
        """
        :param layers:
        """
        super(ScaleChannel, self).__init__()
        self.layers = layers  # layer indices

    def forward(self, x, outputs):
        """
        :param x:
        :param outputs:
        :return:
        """
        a = outputs[self.layers[0]]
        return x.expand_as(a) * a
        # return torch.mul(a, x)


# scaled_channels layer: my implemention
class ScaleChannels(nn.Module):
    def __init__(self, layers):
        super(ScaleChannels, self).__init__()
        self.layers = layers

        # assert len(self.layers) == 1

    def forward(self, x, outputs):
        # Scalar is current input: x
        # H×W = 1×1
        # assert x.shape[2] == 1 and x.shape[3] == 1

        layer = outputs[self.layers[0]]

        # assert x.shape[1] == layer.shape[1]  # make sure channels dim are the same

        # Do Scaling: applying broadcasting here
        x = x * layer

        return x


# Dropout layer
class Dropout(nn.Module):
    def __init__(self, prob):
        """
        :param prob:
        """
        super(Dropout, self).__init__()
        self.prob = float(prob)

    def forward(self, x):
        """
        :param x:
        :return:
        """
        return F.dropout(x, p=self.prob)


# To do global average pooling: my implemention
class GlobalAvgPool(nn.Module):
    def __init__(self):
        super(GlobalAvgPool, self).__init__()

    def forward(self, x):
        return F.adaptive_avg_pool2d(x, (1, 1))  # set output size (1, 1)


class GAP(nn.Module):
    def __init__(self, dimension=1):
        super(GAP, self).__init__()
        self.avg_pool = nn.AdaptiveAvgPool2d(1)

    def forward(self, x):
        return self.avg_pool(x)


class FeatureConcat(nn.Module):
    def __init__(self, layers):
        """
        :param layers:
        """
        super(FeatureConcat, self).__init__()
        self.layers = layers  # layer indices
        self.multiple = len(layers) > 1  # multiple layers flag

    def forward(self, x, outputs):
        """
        :param x:
        :param outputs:
        :return:
        """
        return torch.cat([outputs[i] for i in self.layers], 1) if self.multiple else outputs[self.layers[0]]


class FeatureConcat_l(nn.Module):
    def __init__(self, layers):
        """
        :param layers:
        """
        super(FeatureConcat_l, self).__init__()
        self.layers = layers  # layer indices
        self.multiple = len(layers) > 1  # multiple layers flag

    def forward(self, x, outputs):
        """
        :param x:
        :param outputs:
        :return:
        """
        return torch.cat([outputs[i][:, :outputs[i].shape[1] // 2, :, :] for i in self.layers], 1) if self.multiple else \
            outputs[self.layers[0]][:, :outputs[self.layers[0]].shape[1] // 2, :, :]


class WeightedFeatureFusion(nn.Module):  # weighted sum of 2 or more layers https://arxiv.org/abs/1911.09070
    def __init__(self, layers, weight=False):
        """
        :param layers:
        :param weight:
        """
        super(WeightedFeatureFusion, self).__init__()
        self.layers = layers  # layer indices
        self.weight = weight  # apply weights boolean
        self.n = len(layers) + 1  # number of layers
        if weight:
            self.w = nn.Parameter(torch.zeros(self.n), requires_grad=True)  # layer weights

    def forward(self, x, outputs):
        """
        :param x:
        :param outputs:
        :return:
        """
        # Weights
        if self.weight:
            w = torch.sigmoid(self.w) * (2 / self.n)  # sigmoid weights (0-1)
            x = x * w[0]

        # Fusion
        nx = x.shape[1]  # input channels
        for i in range(self.n - 1):
            a = outputs[self.layers[i]] * w[i + 1] if self.weight else outputs[self.layers[i]]  # feature to add
            na = a.shape[1]  # feature channels

            # Adjust channels
            if nx == na:  # same shape
                x = x + a
            elif nx > na:  # slice input
                x[:, :na] = x[:, :na] + a  # or a = nn.ZeroPad2d((0, 0, 0, 0, 0, dc))(a); x = x + a
            else:  # slice feature
                x = x + a[:, :nx]

        return x


class MixConv2d(nn.Module):  # MixConv: Mixed Depthwise Convolutional Kernels https://arxiv.org/abs/1907.09595
    def __init__(self, in_ch, out_ch, k=(3, 5, 7), stride=1, dilation=1, bias=True, method='equal_params'):
        super(MixConv2d, self).__init__()

        groups = len(k)
        if method == 'equal_ch':  # equal channels per group
            i = torch.linspace(0, groups - 1E-6, out_ch).floor()  # out_ch indices
            ch = [(i == g).sum() for g in range(groups)]
        else:  # 'equal_params': equal parameter count per group
            b = [out_ch] + [0] * groups
            a = np.eye(groups + 1, groups, k=-1)
            a -= np.roll(a, 1, axis=1)
            a *= np.array(k) ** 2
            a[0] = 1
            ch = np.linalg.lstsq(a, b, rcond=None)[0].round().astype(int)  # solve for equal weight indices, ax = b

        self.m = nn.ModuleList([nn.Conv2d(in_channels=in_ch,
                                          out_channels=ch[g],
                                          kernel_size=k[g],
                                          stride=stride,
                                          padding=k[g] // 2,  # 'same' pad
                                          dilation=dilation,
                                          bias=bias) for g in range(groups)])

    def forward(self, x):
        return torch.cat([m(x) for m in self.m], 1)


class MixDeConv2d(nn.Module):  # MixDeConv: Mixed Depthwise DeConvolutional Kernels https://arxiv.org/abs/1907.09595
    def __init__(self, in_ch, out_ch, k=(3, 5, 7), stride=1, dilation=1, bias=True, method='equal_params'):
        """
        :param in_ch:
        :param out_ch:
        :param k:
        :param stride:
        :param dilation:
        :param bias:
        :param method:
        """
        super(MixDeConv2d, self).__init__()

        groups = len(k)
        if method == 'equal_ch':  # equal channels per group
            i = torch.linspace(0, groups - 1E-6, out_ch).floor()  # out_ch indices
            ch = [(i == g).sum() for g in range(groups)]
        else:  # 'equal_params': equal parameter count per group
            b = [out_ch] + [0] * groups
            a = np.eye(groups + 1, groups, k=-1)
            a -= np.roll(a, 1, axis=1)
            a *= np.array(k) ** 2
            a[0] = 1
            ch = np.linalg.lstsq(a, b, rcond=None)[0].round().astype(int)  # solve for equal weight indices, ax = b

        self.m = nn.ModuleList([nn.ConvTranspose2d(in_channels=in_ch,
                                                   out_channels=ch[g],
                                                   kernel_size=k[g],
                                                   stride=stride,
                                                   padding=k[g] // 2,  # 'same' pad
                                                   dilation=dilation,
                                                   bias=bias) for g in range(groups)])

    def forward(self, x):
        """
        :param x:
        :return:
        """
        return torch.cat([m(x) for m in self.m], 1)


# Activation functions below -------------------------------------------------------------------------------------------
class SwishImplementation(torch.autograd.Function):
    @staticmethod
    def forward(ctx, x):
        ctx.save_for_backward(x)
        return x * torch.sigmoid(x)

    @staticmethod
    def backward(ctx, grad_output):
        x = ctx.saved_tensors[0]
        sx = torch.sigmoid(x)  # sigmoid(ctx)
        return grad_output * (sx * (1 + x * (1 - sx)))


class MishImplementation(torch.autograd.Function):
    @staticmethod
    def forward(ctx, x):
        ctx.save_for_backward(x)
        return x.mul(torch.tanh(F.softplus(x)))  # x * tanh(ln(1 + exp(x)))

    @staticmethod
    def backward(ctx, grad_output):
        x = ctx.saved_tensors[0]
        sx = torch.sigmoid(x)
        fx = F.softplus(x).tanh()
        return grad_output * (fx + x * sx * (1 - fx * fx))


class MemoryEfficientSwish(nn.Module):
    def forward(self, x):
        return SwishImplementation.apply(x)


class MemoryEfficientMish(nn.Module):
    def forward(self, x):
        return MishImplementation.apply(x)


class Swish(nn.Module):
    def forward(self, x):
        return x * torch.sigmoid(x)


class HardSwish(nn.Module):  # https://arxiv.org/pdf/1905.02244.pdf
    def forward(self, x):
        return x * F.hardtanh(x + 3, 0., 6., True) / 6.
