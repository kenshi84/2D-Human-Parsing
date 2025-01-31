import socket
import timeit
import numpy as np
from PIL import Image
from datetime import datetime
import os
import os.path as osp
import sys
from collections import OrderedDict
sys.path.append('../')
# PyTorch includes
import torch
from torch.autograd import Variable
from torchvision import transforms
import cv2
import time


# Custom includes
from networks import deeplab_xception_transfer, graph
from dataloaders import custom_transforms as tr

#
import argparse
import torch.nn.functional as F

label_colours = [(0,0,0)
                , (128,0,0), (255,0,0), (0,85,0), (170,0,51), (255,85,0), (0,0,85), (0,119,221), (85,85,0), (0,85,85), (85,51,0), (52,86,128), (0,128,0)
                , (0,0,255), (51,170,221), (0,255,255), (85,255,170), (170,255,85), (255,255,0), (255,170,0)]

label_colours_palette = [0,0,0
                , 128,0,0, 255,0,0, 0,85,0, 170,0,51, 255,85,0, 0,0,85, 0,119,221, 85,85,0, 0,85,85, 85,51,0, 52,86,128, 0,128,0
                , 0,0,255, 51,170,221, 0,255,255, 85,255,170, 170,255,85, 255,255,0, 255,170,0]

label_colours_palette += [255] * ((256-20)*3)
label_colours_palette_img = Image.new('P', (16, 16))
label_colours_palette_img.putpalette(label_colours_palette)

def flip(x, dim):
    indices = [slice(None)] * x.dim()
    indices[dim] = torch.arange(x.size(dim) - 1, -1, -1,
                                dtype=torch.long, device=x.device)
    return x[tuple(indices)]

def flip_cihp(tail_list):
    '''

    :param tail_list: tail_list size is 1 x n_class x h x w
    :return:
    '''
    # tail_list = tail_list[0]
    tail_list_rev = [None] * 20
    for xx in range(14):
        tail_list_rev[xx] = tail_list[xx].unsqueeze(0)
    tail_list_rev[14] = tail_list[15].unsqueeze(0)
    tail_list_rev[15] = tail_list[14].unsqueeze(0)
    tail_list_rev[16] = tail_list[17].unsqueeze(0)
    tail_list_rev[17] = tail_list[16].unsqueeze(0)
    tail_list_rev[18] = tail_list[19].unsqueeze(0)
    tail_list_rev[19] = tail_list[18].unsqueeze(0)
    return torch.cat(tail_list_rev,dim=0)


def decode_labels(mask, num_images=1, num_classes=20):
    """Decode batch of segmentation masks.

    Args:
      mask: result of inference after taking argmax.
      num_images: number of images to decode from the batch.
      num_classes: number of classes to predict (including background).

    Returns:
      A batch with num_images RGB images of the same size as the input.
    """
    n, h, w = mask.shape
    assert (n >= num_images), 'Batch size %d should be greater or equal than number of images to save %d.' % (
    n, num_images)
    outputs = np.zeros((num_images, h, w, 3), dtype=np.uint8)
    for i in range(num_images):
        img = Image.new('RGB', (len(mask[i, 0]), len(mask[i])))
        pixels = img.load()
        for j_, j in enumerate(mask[i, :, :]):
            for k_, k in enumerate(j):
                if k < num_classes:
                    pixels[k_, j_] = label_colours[k]
        outputs[i] = np.array(img)
    return outputs

def read_img(img_path):
    _img = Image.open(img_path).convert('RGB')  # return is RGB pic
    return _img

def img_transform(img, transform=None):
    sample = {'image': img, 'label': 0}

    sample = transform(sample)
    return sample

def inference(net, input_path, output_path, use_gpu=True):
    '''

    :param net:
    :param input_path:
    :param output_path:
    :return:
    '''
    start_time = timeit.default_timer()
    # adj
    adj2_ = torch.from_numpy(graph.cihp2pascal_nlp_adj).float()
    # adj2 = adj2_.unsqueeze(0).unsqueeze(0).expand(opts.gpus, 1, 7, 20).transpose(2, 3)
    adj2_test = adj2_.unsqueeze(0).unsqueeze(0).expand(1, 1, 7, 20).cuda().transpose(2, 3)

    adj1_ = Variable(torch.from_numpy(graph.preprocess_adj(graph.pascal_graph)).float())
    # adj3 = adj1_.unsqueeze(0).unsqueeze(0).expand(opts.gpus, 1, 7, 7)
    adj3_test = adj1_.unsqueeze(0).unsqueeze(0).expand(1, 1, 7, 7).cuda()

    # adj2 = torch.from_numpy(graph.cihp2pascal_adj).float()
    # adj2 = adj2.unsqueeze(0).unsqueeze(0).expand(opts.gpus, 1, 7, 20)
    cihp_adj = graph.preprocess_adj(graph.cihp_graph)
    adj3_ = Variable(torch.from_numpy(cihp_adj).float())
    # adj1 = adj3_.unsqueeze(0).unsqueeze(0).expand(opts.gpus, 1, 20, 20)
    adj1_test = adj3_.unsqueeze(0).unsqueeze(0).expand(1, 1, 20, 20).cuda()

    # multi-scale
    scale_list = [1, 0.5, 0.75, 1.25, 1.5, 1.75]
    img = read_img(input_path)
    testloader_list = []
    testloader_flip_list = []
    for pv in scale_list:
        composed_transforms_ts = transforms.Compose([
            # tr.Keep_origin_size_Resize(max_size=(1024, 1024)),
            # tr.Normalize(mean=(0.485, 0.456, 0.406), std=(0.229, 0.224, 0.225)),
            tr.Scale_only_img(pv),
            tr.Normalize_xception_tf_only_img(),
            tr.ToTensor_only_img()])

        composed_transforms_ts_flip = transforms.Compose([
            # tr.Keep_origin_size_Resize(max_size=(1024, 1024)),
            # tr.Normalize(mean=(0.485, 0.456, 0.406), std=(0.229, 0.224, 0.225)),
            tr.Scale_only_img(pv),
            tr.HorizontalFlip_only_img(),
            tr.Normalize_xception_tf_only_img(),
            tr.ToTensor_only_img()])

        testloader_list.append(img_transform(img, composed_transforms_ts))
        # print(img_transform(img, composed_transforms_ts))
        testloader_flip_list.append(img_transform(img, composed_transforms_ts_flip))
    # print(testloader_list)
    # Main Training and Testing Loop
    for epoch in range(1):
        # start_time = timeit.default_timer()
        # One testing epoch
        net.eval()
        # 1 0.5 0.75 1.25 1.5 1.75 ; flip:

        for iii, sample_batched in enumerate(zip(testloader_list, testloader_flip_list)):
            inputs, labels = sample_batched[0]['image'], sample_batched[0]['label']
            inputs_f, _ = sample_batched[1]['image'], sample_batched[1]['label']
            inputs = inputs.unsqueeze(0)
            inputs_f = inputs_f.unsqueeze(0)
            inputs = torch.cat((inputs, inputs_f), dim=0)
            if iii == 0:
                _, _, h, w = inputs.size()
            # assert inputs.size() == inputs_f.size()

            # Forward pass of the mini-batch
            inputs = Variable(inputs, requires_grad=False)

            with torch.no_grad():
                if use_gpu >= 0:
                    inputs = inputs.cuda()
                # outputs = net.forward(inputs)
                outputs = net.forward(inputs, adj1_test.cuda(), adj3_test.cuda(), adj2_test.cuda())
                outputs = (outputs[0] + flip(flip_cihp(outputs[1]), dim=-1)) / 2
                outputs = outputs.unsqueeze(0)

                if iii > 0:
                    outputs = F.upsample(outputs, size=(h, w), mode='bilinear', align_corners=True)
                    outputs_final = outputs_final + outputs
                else:
                    outputs_final = outputs.clone()
        ################ plot pic
        predictions = torch.max(outputs_final, 1)[1]
        results = predictions.cpu().numpy()
        vis_res = decode_labels(results)

        parsing_im = Image.fromarray(vis_res[0])
        # https://stackoverflow.com/a/62899187
        parsing_im = parsing_im.quantize(palette=label_colours_palette_img, dither=0)
        parsing_im.save(output_path)
        # cv2.imwrite(output_path+'/{}_gray.png'.format(output_name), results[0, :, :])

        end_time = timeit.default_timer()
        print('time use for image' + ' is :' + str(end_time - start_time))

def inference_dir(loadmodel, input_dir, output_dir):
    net = deeplab_xception_transfer.deeplab_xception_transfer_projection_v3v5_more_savemem(n_classes=20, os=16,
                                                                                   hidden_layers=128,
                                                                                   source_classes=7,)

    x = torch.load(loadmodel)
    net.load_source_model(x)
    print('load model:', loadmodel)

    net.cuda()
    if not os.path.exists(output_dir):
        os.makedirs(output_dir)

    if not osp.exists(input_dir):
        raise RuntimeError('input_dir must exist!!!!')

    # List all .jpg files in input_dir:
    img_list = [f for f in os.listdir(input_dir) if osp.isfile(osp.join(input_dir, f)) and f.endswith('.jpg')]

    total = len(img_list)
    sstime = time.time()
    i = 1
    showFreq = 200
    for img in img_list:
        single_ss = time.time()
        img_name = osp.splitext(img)[0]
        input_path = osp.join(input_dir, f'{img_name}.jpg')
        output_path = osp.join(output_dir, f'{img_name}.png')
        inference(net=net, input_path=input_path, output_path=output_path, use_gpu=True)
        if i % showFreq == 0:
            exp_time = time.time() - sstime
            print('{}/{} Finish, total time:{}'.format(str(i), str(total), str(exp_time)))
        single_ee = time.time()
        print('total time for single image ', single_ee - single_ss)
        i = i + 1

if __name__ == '__main__':
    '''argparse begin'''
    parser = argparse.ArgumentParser()
    parser.add_argument('--loadmodel', required=True, type=str)
    parser.add_argument('--input_dir', required=True, type=str)
    parser.add_argument('--output_dir', required=True, type=str)
    opts = parser.parse_args()

    inference_dir(opts.loadmodel, opts.input_dir, opts.output_dir)
