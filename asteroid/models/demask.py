from torch import nn
from .base_models import BaseModel
from asteroid.filterbanks import make_enc_dec
from asteroid.masknn import norms, activations
from asteroid.filterbanks.transforms import take_mag, take_cat
from .. import torch_utils


class DeMask(BaseModel):
    """
    Simple MLP model for surgical mask speech enhancement A transformed-domain masking approach is used.
    Args:
        input_type (str, optional): whether the magnitude spectrogram "mag" or both real imaginary parts "reim" are
                    passed as features to the masker network.
                    Concatenation of "mag" and "reim" also can be used by using "cat".
        output_type (str, optional): whether the masker ouputs a mask
                    for magnitude spectrogram "mag" or both real imaginary parts "reim".

        hidden_dims (list, optional): list of MLP hidden layer sizes.
        dropout (float, optional): dropout probability.
        activation (str, optional): type of activation used in hidden MLP layers.
        mask_act (str, optional): Which non-linear function to generate mask.
        norm_type (str, optional): To choose from ``'BN'``, ``'gLN'``,
            ``'cLN'``.

        fb_name (str): type of analysis and synthesis filterbanks used,
                            choose between ["stft", "free", "analytic_free"].
        n_filters (int): number of filters in the analysis and synthesis filterbanks.
        stride (int): filterbank filters stride.
        kernel_size (int): length of filters in the filterbank.
        encoder_activation (str)
        **fb_kwargs (dict): Additional kwards to pass to the filterbank
            creation.
    """

    def __init__(
        self,
        input_type="mag",
        output_type="mag",
        hidden_dims=[1024],
        dropout=0,
        activation="relu",
        mask_act="relu",
        norm_type="gLN",
        n_filters=512,
        stride=256,
        kernel_size=512,
        **fb_kwargs,
    ):

        super().__init__()

        self.input_type = input_type
        self.output_type = output_type

        self.encoder, self.decoder = make_enc_dec(
            "stft", kernel_size=kernel_size, n_filters=n_filters, stride=stride, **fb_kwargs
        )

        if self.input_type == "mag":
            n_feats_input = (self.encoder.filterbank.n_filters) // 2 + 1
        elif self.input_type == "cat":
            n_feats_input = (
                (self.encoder.filterbank.n_filters // 2) + 1 + self.encoder.filterbank.n_filters
            )
        elif self.input_type == "reim":
            n_feats_input = self.encoder.filterbank.n_filters
        else:
            print("Input type should be either mag, reim or cat")
            raise NotImplementedError

        if self.output_type == "mag":
            n_feats_output = self.encoder.filterbank.n_filters // 2 + 1
        elif self.input_type == "reim":
            n_feats_output = self.encoder.filterbank.n_filters
        else:
            print("Input type should be either mag or reim")
            raise NotImplementedError

        net = [norms.get(norm_type)(n_feats_input)]
        in_chan = n_feats_input
        for layer in range(len(hidden_dims)):
            net.extend(
                [
                    nn.Conv1d(in_chan, hidden_dims[layer], 1),
                    norms.get(norm_type)(hidden_dims[layer]),
                    activations.get(activation)(),
                    nn.Dropout(dropout),
                ]
            )
            in_chan = hidden_dims[layer]

        net.extend([nn.Conv1d(in_chan, n_feats_output, 1), activations.get(mask_act)()])

        self.masker = nn.Sequential(*net)

    def forward(self, wav):

        # Handle 1D, 2D or n-D inputs
        was_one_d = False
        if wav.ndim == 1:
            was_one_d = True
            wav = wav.unsqueeze(0).unsqueeze(1)
        if wav.ndim == 2:
            wav = wav.unsqueeze(1)
        # Real forward
        tf_rep = self.encoder(wav)
        if self.input_type == "mag":
            est_masks = self.masker(take_mag(tf_rep))
        elif self.input_type == "reim":
            est_masks = self.masker(tf_rep)
        elif self.input_type == "cat":
            est_masks = self.masker(take_cat(tf_rep))
        else:
            raise NotImplementedError

        if self.output_type == "mag":
            masked_tf_rep = est_masks.repeat(1, 2, 1) * tf_rep
        elif self.output_type == "reim":
            masked_tf_rep = est_masks * tf_rep.unsqueeze(1)
        else:
            raise NotImplementedError

        out_wavs = torch_utils.pad_x_to_y(self.decoder(masked_tf_rep), wav)
        if was_one_d:
            return out_wavs.squeeze(0)
        return out_wavs

    def get_model_args(self):
        """ Arguments needed to re-instantiate the model. """
        fb_config = self.encoder.filterbank.get_config()
        masknet_config = self.masker.get_config()
        # Assert both dict are disjoint
        if not all(k not in fb_config for k in masknet_config):
            raise AssertionError(
                "Filterbank and Mask network config share" "common keys. Merging them is not safe."
            )
        # Merge all args under model_args.
        model_args = {
            **fb_config,
            **masknet_config,
            "encoder_activation": self.encoder_activation,
        }
        return model_args
