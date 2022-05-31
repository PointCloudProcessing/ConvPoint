# Semantic3D Example with ConvPoint

# add the parent folder to the python path to access convpoint library
import sys
sys.path.append('../../')
from utils import metrics as metrics
import convpoint.knn.lib.python.nearest_neighbors as nearest_neighbors

# from convpoint.knn.lib.python import nearest_neighbors as nearest_neighbors

import numpy as np
import argparse
from datetime import datetime
import os
import random
from tqdm import tqdm


import torch
import torch.utils.data
import torch.nn.functional as F
from torchvision import transforms

from sklearn.metrics import confusion_matrix
import time
from torch.utils.data import TensorDataset, ConcatDataset
import logging

# import convpoint.knn.lib.python.nearest_neighbors as nearest_neighbors

from PIL import Image
torch.cuda.empty_cache()
import gc

gc.collect()
torch.cuda.memory_summary(device=None, abbreviated=False)

class bcolors:
    HEADER = '\033[95m'
    OKBLUE = '\033[94m'
    OKGREEN = '\033[92m'
    WARNING = '\033[93m'
    FAIL = '\033[91m'
    ENDC = '\033[0m'
    BOLD = '\033[1m'
    UNDERLINE = '\033[4m'

# wrap blue / green
def wblue(str):
    return bcolors.OKBLUE+str+bcolors.ENDC
def wgreen(str):
    return bcolors.OKGREEN+str+bcolors.ENDC

def nearest_correspondance(pts_src, pts_dest, data_src, K=1):
    print(pts_dest)
    indices = nearest_neighbors.knn(pts_src.copy(), pts_dest.copy(), K, omp=True)
    print(indices)
    if K==1:
        indices = indices.ravel()
        data_dest = data_src[indices]
    else:
        data_dest = data_src[indices].mean(1)
    return data_dest

def rotate_point_cloud_z(batch_data):
    """ Randomly rotate the point clouds to augument the dataset
        rotation is per shape based along up direction
        Input:
          BxNx3 array, original batch of point clouds
        Return:
          BxNx3 array, rotated batch of point clouds
    """
    rotation_angle = np.random.uniform() * 2 * np.pi
    cosval = np.cos(rotation_angle)
    sinval = np.sin(rotation_angle)
    rotation_matrix = np.array([[cosval, sinval, 0],
                                [-sinval, cosval, 0],
                                [0, 0, 1],])
    return np.dot(batch_data, rotation_matrix)

# Part dataset only for training / validation
class PartDataset():

    def __init__ (self, filelist, folder,
                    training=False, 
                    iteration_number = None,
                    block_size=8,
                    npoints = 8192,
                    nocolor=True,
                    transfer=False):

        self.folder = folder
        self.training = training
        self.filelist = filelist
        self.bs = block_size
        self.nocolor = nocolor

        self.npoints = npoints
        self.iterations = iteration_number
        self.verbose = False
        self.transfer = transfer

        
        self.transform = transforms.ColorJitter(
            brightness=0.4,
            contrast=0.4,
            saturation=0.4)

    def __getitem__(self, index):
        
        # load the data
        index = random.randint(0, len(self.filelist)-1)
        pts = np.load(os.path.join(self.folder, self.filelist[index]))

        # get the features
        fts = pts[:,3:6]

        # get the labels
        lbs = pts[:, 6].astype(int)-1 # the generation script label starts at 1

        # get the point coordinates
        pts = pts[:, :3]


        # pick a random point
        pt_id = random.randint(0, pts.shape[0]-1)
        pt = pts[pt_id]

        # create the mask
        mask_x = np.logical_and(pts[:,0]<pt[0]+self.bs/2, pts[:,0]>pt[0]-self.bs/2)
        mask_y = np.logical_and(pts[:,1]<pt[1]+self.bs/2, pts[:,1]>pt[1]-self.bs/2)
        mask = np.logical_and(mask_x, mask_y)
        pts = pts[mask]
        lbs = lbs[mask]
        fts = fts[mask]
        
        # random selection
        choice = np.random.choice(pts.shape[0], self.npoints, replace=True)
        pts = pts[choice]
        lbs = lbs[choice]
        fts = fts[choice]

        # data augmentation
        if self.training:
            # random rotation
            pts = rotate_point_cloud_z(pts)

            # random jittering
            fts = fts.astype(np.uint8)
            fts = np.array(self.transform( Image.fromarray(np.expand_dims(fts, 0)) ))
            fts = np.squeeze(fts, 0)
        
        fts = fts.astype(np.float32)
        fts = fts / 255 - 0.5

        if self.nocolor:
            fts = np.ones((pts.shape[0], 1))

        pts = torch.from_numpy(pts).float()
        fts = torch.from_numpy(fts).float()
        lbs = torch.from_numpy(lbs).long()

        if self.transfer==True:
            clabel = torch.from_numpy(np.zeros(lbs.shape[0])).float()
        if self.transfer==False:
            clabel = torch.from_numpy(np.ones(lbs.shape[0])).float()

        return pts, fts, lbs, clabel

    def __len__(self):
        return self.iterations

class PartDatasetTest():

    def compute_mask(self, pt, bs):
        # build the mask
        mask_x = np.logical_and(self.xyzrgb[:,0]<pt[0]+bs/2, self.xyzrgb[:,0]>pt[0]-bs/2)
        mask_y = np.logical_and(self.xyzrgb[:,1]<pt[1]+bs/2, self.xyzrgb[:,1]>pt[1]-bs/2)
        mask = np.logical_and(mask_x, mask_y)
        return mask

    def __init__ (self, filename, folder,
                    block_size=8,
                    npoints = 8192,
                    test_step=0.8, nocolor=True):

        self.folder = folder
        self.bs = block_size
        self.npoints = npoints
        self.verbose = False
        self.nocolor = nocolor
        self.filename = filename

        # load the points
        self.xyzrgb = np.load(os.path.join(self.folder, self.filename))
        step = test_step
        discretized = ((self.xyzrgb[:,:2]).astype(float)/step).astype(int)
        self.pts = np.unique(discretized, axis=0)
        self.pts = self.pts.astype(np.float)*step

    def __getitem__(self, index):
        # index = random.randint(0, len(self.pts)-1)
        # get the data
        mask = self.compute_mask(self.pts[index], self.bs)
        pts = self.xyzrgb[mask]

        # choose right number of points
        choice = np.random.choice(pts.shape[0], self.npoints, replace=True)
        pts = pts[choice]

        # labels will contain indices in the original point cloud
        lbs = np.where(mask)[0][choice]

        # separate between features and points
        if self.nocolor:
            fts = np.ones((pts.shape[0], 1))
        else:
            # fts = pts[:,3:6]
            fts = np.zeros((pts.shape[0], 3))
            fts[:,2]=np.ones((pts.shape[0]))
            fts = fts.astype(np.float32)
            fts = fts / 255 - 0.5

        pts = pts[:, :3].copy()

        pts = torch.from_numpy(pts).float()
        fts = torch.from_numpy(fts).float()
        lbs = torch.from_numpy(lbs).long()

        return pts, fts, lbs

    def __len__(self):
        return len(self.pts)


def get_model(model_name, input_channels, output_channels, args):
    if model_name == "SegBig":
        from networks.network_seg import SegBig as Net
        return Net(input_channels, output_channels, args=args)
    elif model_name == "SegSmall":
        from networks.network_seg import SegSmall as Net
        return Net(input_channels, output_channels)
    elif model_name == "Disctiminiator":
        from networks.network_seg import SegSmall_Discriminator as Net
        return Net(input_channels, output_channels)
        
class TqdmLoggingHandler(logging.Handler):
    def __init__(self, level=logging.NOTSET):
        super().__init__(level)

    def emit(self, record):
        try:
            msg = self.format(record)
            tqdm.write(msg)
            self.flush()
        except Exception:
            self.handleError(record)  


def validation(net, filelist_test):
    ##### TEST

    net.eval()

    for filename in filelist_test:
        # print(filename)
        ds = PartDatasetTest(filename, args.rootdir,
                        block_size=args.block_size,
                        npoints= args.npoints,
                        test_step=args.test_step,
                        nocolor=args.nocolor
                        )
        loader = torch.utils.data.DataLoader(ds, batch_size=args.batch_size, shuffle=False,
                                        num_workers=args.threads
                                        )

        xyzrgb = ds.xyzrgb[:,:3]
        scores = np.zeros((xyzrgb.shape[0], N_CLASSES))
        with torch.no_grad():
            t = tqdm(loader, ncols=100)
            for pts, features, indices,_ in t:
                
                features = features.cuda()
                #print(features)
                #print(pts)
                pts = pts.cuda()
                outputs,_ = net(features, pts)
                # print(outputs)
                outputs_np = outputs.cpu().numpy().reshape((-1, N_CLASSES))
                
                scores[indices.cpu().numpy().ravel()] += outputs_np
                #print(scores[indices[0][0]])
        
        mask = np.logical_not(scores.sum(1)==0)
        #print(mask)
        scores = scores[mask]
        
        pts_src = xyzrgb[mask]

        # create the scores for all points
        scores = nearest_correspondance(pts_src.astype(np.float32), xyzrgb.astype(np.float32), scores, K=1)

        # compute softmax
        scores = scores - scores.max(axis=1)[:,None]
        scores = np.exp(scores) / np.exp(scores).sum(1)[:,None]
        scores = np.nan_to_num(scores)

        os.makedirs(os.path.join(args.savedir, "results"), exist_ok=True)

        # saving labels
        save_fname = os.path.join(args.savedir, "results", filename.replace(".npy",".labels"))
        scores = scores.argmax(1)
        np.savetxt(save_fname,scores,fmt='%d')

        if args.savepts:
            save_fname = os.path.join(args.savedir, "results", f"{filename}_pts.txt")
            xyzrgb = np.concatenate([xyzrgb, np.expand_dims(scores,1)], axis=1)
            np.savetxt(save_fname,xyzrgb,fmt=['%.4f','%.4f','%.4f','%d'])


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--rootdir', '-s', help='Path to data folder')
    parser.add_argument("--savedir", type=str, default="./results")
    parser.add_argument('--block_size', help='Block size', type=float, default=16)
    parser.add_argument("--epochs", type=int, default=101)
    parser.add_argument("--batch_size", "-b", type=int, default=16)
    parser.add_argument("--iter", "-i", type=int, default=1200)
    parser.add_argument("--npoints", "-n", type=int, default=8192)
    parser.add_argument("--threads", type=int, default=4)
    parser.add_argument("--nocolor",default=True)
    parser.add_argument("--test", action="store_true")
    parser.add_argument("--savepts", action="store_true")
    parser.add_argument("--test_step", default=0.8, type=float)
    parser.add_argument("--model", default="Disctiminiator", type=str)
    parser.add_argument("--drop", default=0.5, type=float)
    args = parser.parse_args()

    time_string = datetime.now().strftime('%Y-%m-%d-%H-%M-%S')
    
    root_folder = os.path.join(args.savedir, "{}_{}_nocolor{}_drop{}_{}".format(
            args.model, args.npoints, args.nocolor, args.drop, time_string))

    filelist_train=[
        "mls2016_8class_20cm_ascii_area2_voxels.npy",

    ]
    filelist_train_trans=[
        "bildstein_station1_xyz_intensity_rgb_voxels.npy",
        "bildstein_station3_xyz_intensity_rgb_voxels.npy",
        "domfountain_station1_xyz_intensity_rgb_voxels.npy",
        "domfountain_station3_xyz_intensity_rgb_voxels.npy",
        "neugasse_station1_xyz_intensity_rgb_voxels.npy",
        "sg27_station1_intensity_rgb_voxels.npy",
        "sg27_station2_intensity_rgb_voxels.npy",
        "untermaederbrunnen_station1_xyz_intensity_rgb_voxels.npy",
    ]
    

    filelist_val=[
        #"area3_voxels.npy",
        "mls2016_8class_20cm_ascii_area1_voxels.npy",
        #"mls2016_8class_20cm_ascii_area1_voxels.npy",
    ]
    print(filelist_train,filelist_train_trans)
    print(filelist_val)

    N_CLASSES= 8

    print(args.model)
    # create model
    print("Creating the network...", end="", flush=True)
    if args.nocolor:
        net = get_model(args.model, input_channels=1, output_channels=N_CLASSES, args=args)
    else:
        net = get_model(args.model, input_channels=3, output_channels=N_CLASSES, args=args)
    if args.test:
        net.load_state_dict(torch.load(os.path.join(args.savedir, "state_dict.pth")))
    net.cuda()
    print("Done")
    print("discriminator output 1 class(Linear)")
    print("discriminator at layer 6 features")
    # log
    log = logging.getLogger(__name__)
    log.setLevel(logging.INFO)
    log.addHandler(TqdmLoggingHandler())

    ##### TRAIN
    if not args.test:
        print("Create the datasets...", end="", flush=True)

        ds = PartDataset(filelist_train, args.rootdir,
                                training=True, block_size=args.block_size,
                                iteration_number=args.batch_size*args.iter,  #16000
                                npoints=args.npoints,
                                nocolor=args.nocolor,
                                transfer=False)

        ds_transfer = PartDataset(filelist_train_trans, args.rootdir,
                                training=True, block_size=args.block_size,
                                iteration_number=args.batch_size*args.iter,
                                npoints=args.npoints,
                                nocolor=args.nocolor,
                                transfer=True)
        
        train_loader = torch.utils.data.DataLoader(ds, batch_size=args.batch_size, shuffle=True,
                                            num_workers=args.threads)

        train_trans_loader = torch.utils.data.DataLoader(ds_transfer, batch_size=args.batch_size, shuffle=True,
                                            num_workers=args.threads)
        val = PartDataset(filelist_val, args.rootdir,
                                training=True, block_size=args.block_size,
                                iteration_number=args.batch_size*args.iter,
                                npoints=args.npoints,
                                nocolor=args.nocolor,
                                transfer=True)
        val_loader = torch.utils.data.DataLoader(val, batch_size=args.batch_size, shuffle=True,
                                            num_workers=args.threads)
        print("Done")

        print("Create optimizer...", end="", flush=True)
        optimizer = torch.optim.Adam(net.parameters(), lr=1e-3)
        print("Done")
        
        # create the root folder
        os.makedirs(root_folder, exist_ok=True)
        
        # create the log file
        logs = open(os.path.join(root_folder, "log.txt"), "w")

        best_iou = 0.0
        # iterate over epochs
        for epoch in range(args.epochs):

            #######
            # training
            net.train()

            train_loss = 0
            #trans_loss = 0
            cm = np.zeros((N_CLASSES, N_CLASSES))
            t = tqdm(zip(train_loader,train_trans_loader), ncols=100, desc="Epoch {}".format(epoch))

            for (pts, features, seg, clabel),(pts_trans, features_trans, seg_trans, clabel_trans) in t:

            

                # ---------------------
                #  Train Discriminator Semantic3D
                # --------------------
                features = features.cuda() # n*3
                pts = pts.cuda()  # n*3
                seg = seg.cuda()
                clabel = clabel.cuda()

                optimizer.zero_grad()
                outputs, class_out = net(features, pts)
                #discriminator_loss = F.cross_entropy(class_out.view(-1, 2), clabel.view(-1)) # when output linear 2 node
                #discriminator_loss = F.binary_cross_entropy_with_logits(class_out.view(-1), clabel.view(-1))  # when output 1 node
   
                discriminator_loss = torch.nn.MSELoss()(class_out.view(-1),clabel.view(-1))
                # discriminator_loss.backward()
                seg_loss = F.cross_entropy(outputs.view(-1, N_CLASSES), seg.view(-1))
                              
                # loss = (seg_loss+discriminator_loss)
                #loss.backward()
                #optimizer.step()
                # ---------------------
                #  Train Discriminator TUMMLS
                # ---------------------
                features_trans = features_trans.cuda() # n*3
                pts_trans = pts_trans.cuda()  # n*3
                seg_trans = seg_trans.cuda()
                clabel_trans = clabel_trans.cuda()

                #optimizer.zero_grad()
                outputs_trans, class_out_trans = net(features_trans, pts_trans)
            
                #discriminator_loss_trans = F.cross_entropy(class_out_trans.view(-1, 2), clabel_trans.view(-1)) # when output linear 2 node
                #discriminator_loss_trans = F.binary_cross_entropy_with_logits(class_out_trans.view(-1), clabel_trans.view(-1))
                discriminator_loss_trans = torch.nn.MSELoss()(class_out_trans.view(-1),clabel_trans.view(-1))

                seg_loss_trans = F.cross_entropy(outputs_trans.view(-1, N_CLASSES), seg_trans.view(-1))
                
                # loss = seg_loss+discriminator_loss                
                loss_sum = seg_loss_trans+(seg_loss+discriminator_loss)
                loss_sum.backward()
                optimizer.step()
                
                ## class label
                b_size = class_out.size(0)

                output_np = np.argmax(outputs.cpu().detach().numpy(), axis=2).copy()
                target_np = seg.cpu().numpy().copy()   # (16, 8192)

                cm_ = confusion_matrix(target_np.ravel(), output_np.ravel(), labels=list(range(N_CLASSES)))
                cm += cm_

                oa = f"{metrics.stats_overall_accuracy(cm):.5f}"
                aa = f"{metrics.stats_accuracy_per_class(cm)[0]:.5f}"
                iou = f"{metrics.stats_iou_per_class(cm)[0]:.5f}"

                train_loss += loss_sum.detach().cpu().item()
                #trans_loss += loss_t.detach().cpu().item()
                
                t.set_postfix(OA=wblue(oa), AA=wblue(aa), IOU=wblue(iou), Train_LOSS=wblue(f"{train_loss/cm.sum():.4e}"))

            # write the logs
            logs.write(f"{epoch} {oa} {aa} {iou}\n")
            logs.flush()

            if epoch%5==0:
                net.eval()
                with torch.no_grad(): 
                    val_loss =0
                    cm = np.zeros((N_CLASSES, N_CLASSES))
                    t = tqdm(val_loader, ncols=100, desc="Epoch {}".format(epoch))
                    
                    for pts, features, seg,_ in t:

                        features = features.cuda()
                        pts = pts.cuda()
                        seg = seg.cuda()

                        outputs,_ = net(features, pts)
                        output_np = np.argmax(outputs.cpu().detach().numpy(), axis=2).copy()
                        target_np = seg.cpu().numpy().copy()   # (16, 8192)

                        cm_ = confusion_matrix(target_np.ravel(), output_np.ravel(), labels=list(range(N_CLASSES)))
                        cm += cm_

                        oa = f"{metrics.stats_overall_accuracy(cm):.5f}"
                        aa = f"{metrics.stats_accuracy_per_class(cm)[0]:.5f}"
                        iou = f"{metrics.stats_iou_per_class(cm)[0]:.5f}"

                        iouf = metrics.stats_iou_per_class(cm)[0]          

                        loss =  F.cross_entropy(outputs.view(-1, N_CLASSES), seg.view(-1))
                        val_loss += loss.detach().cpu().item()

                        t.set_postfix(OA=wblue(oa), AA=wblue(aa), IOU=wblue(iou), LOSS=wblue(f"{val_loss/cm.sum():.4e}"))

                    if iouf>best_iou:
                        best_iou = iouf
                        # save the model
                        print("when iou equals ",iou,"save at",os.path.join(root_folder, "state_dict.pth"))
                        torch.save(net.state_dict(), os.path.join(root_folder, "state_dict.pth"))

                        
                    logs.write(f"{epoch} {oa} {aa} {iou} {val_loss}\n")
                    logs.flush()


        logs.close()

    ##### TEST
    else:
        net.eval()
        for filename in filelist_test:
            # print(filename)
            ds = PartDatasetTest(filename, args.rootdir,
                            block_size=args.block_size,
                            npoints= args.npoints,
                            test_step=args.test_step,
                            nocolor=args.nocolor
                            )
            loader = torch.utils.data.DataLoader(ds, batch_size=args.batch_size, shuffle=False,
                                            num_workers=args.threads
                                            )

            xyzrgb = ds.xyzrgb[:,:3]
            scores = np.zeros((xyzrgb.shape[0], N_CLASSES))
            with torch.no_grad():
                t = tqdm(loader, ncols=100)
                for pts, features, indices,_ in t:
                    
                    features = features.cuda()
                    #print(features)
                    #print(pts)
                    pts = pts.cuda()
                    outputs,_ = net(features, pts)
                    # print(outputs)
                    outputs_np = outputs.cpu().numpy().reshape((-1, N_CLASSES))
                    
                    scores[indices.cpu().numpy().ravel()] += outputs_np
                    #print(scores[indices[0][0]])
            
            mask = np.logical_not(scores.sum(1)==0)
            #print(mask)
            scores = scores[mask]
            
            pts_src = xyzrgb[mask]

            # create the scores for all points
            scores = nearest_correspondance(pts_src.astype(np.float32), xyzrgb.astype(np.float32), scores, K=1)

            # compute softmax
            scores = scores - scores.max(axis=1)[:,None]
            scores = np.exp(scores) / np.exp(scores).sum(1)[:,None]
            scores = np.nan_to_num(scores)

            os.makedirs(os.path.join(args.savedir, "results"), exist_ok=True)

            # saving labels
            save_fname = os.path.join(args.savedir, "results", filename.replace(".npy",".labels"))
            scores = scores.argmax(1)
            np.savetxt(save_fname,scores,fmt='%d')

            if args.savepts:
                save_fname = os.path.join(args.savedir, "results", f"{filename}_pts.txt")
                xyzrgb = np.concatenate([xyzrgb, np.expand_dims(scores,1)], axis=1)
                np.savetxt(save_fname,xyzrgb,fmt=['%.4f','%.4f','%.4f','%d'])

            # break

if __name__ == '__main__':
    main()
    print('{}-Done.'.format(datetime.now()))