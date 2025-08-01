import os
import torch
import torchvision.transforms as transforms
import torch.nn.functional as F
from PIL import Image, ImageSequence, ImageOps
from pathlib import Path
import numpy as np
import trimesh as Trimesh
from tqdm import tqdm
import gc

from .direct3d_s2.pipeline import Direct3DS2Pipeline

import folder_paths

import comfy.model_management as mm
from comfy.utils import load_torch_file, ProgressBar, common_upscale
import comfy.utils

script_directory = os.path.dirname(os.path.abspath(__file__))
comfy_path = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))

def get_folders_os(path):
    """
    Returns a list of all immediate subdirectories (folders) within the given path.

    Args:
        path (str): The path to search for folders.

    Returns:
        list: A list of strings, where each string is the name of a folder.
              Returns an empty list if the path does not exist or contains no folders.
    """
    if not os.path.isdir(path):
        print(f"Error: Path '{path}' does not exist or is not a directory.")
        return []

    folders = []
    for item in os.listdir(path):
        item_path = os.path.join(path, item)
        if os.path.isdir(item_path):
            folders.append(item)
    return folders

def parse_string_to_int_list(number_string):
  """
  Parses a string containing comma-separated numbers into a list of integers.

  Args:
    number_string: A string containing comma-separated numbers (e.g., "20000,10000,5000").

  Returns:
    A list of integers parsed from the input string.
    Returns an empty list if the input string is empty or None.
  """
  if not number_string:
    return []

  try:
    # Split the string by comma and convert each part to an integer
    int_list = [int(num.strip()) for num in number_string.split(',')]
    return int_list
  except ValueError as e:
    print(f"Error converting string to integer: {e}. Please ensure all values are valid numbers.")
    return []

def get_picture_files(folder_path):
    """
    Retrieves all picture files (based on common extensions) from a given folder.

    Args:
        folder_path (str): The path to the folder to search.

    Returns:
        list: A list of full paths to the picture files found.
    """
    picture_extensions = ('.jpg', '.jpeg', '.png', '.gif', '.bmp', '.tiff', '.webp')
    picture_files = []

    if not os.path.isdir(folder_path):
        print(f"Error: Folder '{folder_path}' not found.")
        return []
                
    for entry_name in os.listdir(folder_path):
        full_path = os.path.join(folder_path, entry_name)

        # Check if the entry is actually a file (and not a sub-directory)
        if os.path.isfile(full_path):
            file_name, file_extension = os.path.splitext(entry_name)
            if file_extension.lower().endswith(picture_extensions):
                picture_files.append(full_path)                
    return picture_files
    
def get_mesh_files(folder_path, name_filter = None):
    """
    Retrieves all picture files (based on common extensions) from a given folder.

    Args:
        folder_path (str): The path to the folder to search.

    Returns:
        list: A list of full paths to the picture files found.
    """
    mesh_extensions = ('.obj', '.glb')
    mesh_files = []

    if not os.path.isdir(folder_path):
        print(f"Error: Folder '{folder_path}' not found.")
        return []
                    
    for entry_name in os.listdir(folder_path):
        full_path = os.path.join(folder_path, entry_name)

        # Check if the entry is actually a file (and not a sub-directory)
        if os.path.isfile(full_path):
            file_name, file_extension = os.path.splitext(entry_name)
            if file_extension.lower().endswith(mesh_extensions):
                if name_filter is None or name_filter.lower() in file_name.lower():
                    mesh_files.append(full_path)                 
    return mesh_files    

def get_filename_without_extension_os_path(full_file_path):
    """
    Extracts the filename without its extension from a full file path using os.path.

    Args:
        full_file_path (str): The complete path to the file.

    Returns:
        str: The filename without its extension.
    """
    # 1. Get the base name (filename with extension)
    base_name = os.path.basename(full_file_path)
    
    # 2. Split the base name into root (filename without ext) and extension
    file_name_without_ext, _ = os.path.splitext(base_name)
    
    return file_name_without_ext

def pil2tensor(image):
    return torch.from_numpy(np.array(image).astype(np.float32) / 255.0)[None,]
    
def tensor2pil(image):
    return Image.fromarray(np.clip(255. * image.cpu().numpy().squeeze(), 0, 255).astype(np.uint8))
    
def convert_tensor_images_to_pil(images):
    pil_array = []
    
    for image in images:
        pil_array.append(tensor2pil(image))
        
    return pil_array     

class Hy3DDirect3DS2ModelLoader:
    @classmethod
    def INPUT_TYPES(s):
        return {
            "required": {
                "pipeline_path": (["wushuang98/Direct3D-S2"],{"default":"wushuang98/Direct3D-S2"}),
                "subfolder": (["direct3d-s2-v-1-0","direct3d-s2-v-1-1"],{"default":"direct3d-s2-v-1-1"}),
                "use_legacy_config": ("BOOLEAN",{"default":False}),
            },
        }

    RETURN_TYPES = ("HY3DS2PIPELINE", )
    RETURN_NAMES = ("pipeline", )
    FUNCTION = "process"
    CATEGORY = "Hy3DS2Wrapper"

    def process(self, pipeline_path, subfolder, use_legacy_config):
        device = mm.get_torch_device()
        offload_device = mm.unet_offload_device()
        
        pipe = Direct3DS2Pipeline(device)
        pipe.init_config(pipeline_path, subfolder=subfolder, use_legacy_config=use_legacy_config)
        
        return (pipe,) 

class Hy3DRefineMeshWithDirect3DS2:
    @classmethod
    def INPUT_TYPES(s):
        return {
            "required": {
                "pipeline": ("HY3DS2PIPELINE",),
                "image": ("IMAGE",),
                "trimesh": ("TRIMESH",),
                "sdf_resolution": ([512,1024],{"default":1024}),
                "steps": ("INT",{"default":15}),
                "guidance_scale": ("FLOAT",{"default":7.0,"min":0.0,"max":100.0}),                
                "mc_threshold": ("FLOAT",{"default":0.20,"min":0.00,"max":1.00, "step": 0.01}),
                "seed": ("INT",{"default":0,"min":0,"max":0x7fffffff}),
                "max_latent_tokens": ("INT",{"default":100000,"min":0,"max":200000}),
                "scale": ("FLOAT",{"default":0.95,"min":0.01,"max":0.99, "step": 0.01}),
                "remove_interior": ("BOOLEAN",{"default":False}),
            },
        }

    RETURN_TYPES = ("TRIMESH","HY3DS2PIPELINE", )
    RETURN_NAMES = ("trimesh","pipeline", )
    FUNCTION = "process"
    CATEGORY = "Hy3DS2Wrapper"

    def process(self, pipeline, image, trimesh, sdf_resolution, steps, guidance_scale, mc_threshold, seed, max_latent_tokens, scale, remove_interior):
        image = tensor2pil(image)
        if sdf_resolution==1024:
            trimesh = pipeline.refine_1024(image,trimesh,steps,guidance_scale,remove_interior,mc_threshold,seed, max_latent_tokens, scale)
        elif sdf_resolution==512:
            trimesh = pipeline.refine_512(image,trimesh,steps,guidance_scale,remove_interior,mc_threshold,seed, max_latent_tokens, scale)
        else:
            print(f'Unknown sdf_resolution: {sdf_resolution}')
        
        return (trimesh, pipeline, )  

class Hy3DGenerateDenseMeshWithDirect3DS2:
    @classmethod
    def INPUT_TYPES(s):
        return {
            "required": {
                "pipeline": ("HY3DS2PIPELINE",),
                "image": ("IMAGE",),
                "steps": ("INT",{"default":50}),
                "guidance_scale": ("FLOAT",{"default":7.0,"min":0.0,"max":100.0}),
                "mc_threshold": ("FLOAT",{"default":0.20,"min":0.00,"max":1.00, "step": 0.01}),
                "seed": ("INT",{"default":0,"min":0,"max":0x7fffffff}),
            },
        }

    RETURN_TYPES = ("D3DLATENTINDEX","HY3DS2PIPELINE", )
    RETURN_NAMES = ("latent_index", "pipeline", )
    FUNCTION = "process"
    CATEGORY = "Hy3DS2Wrapper"

    def process(self, pipeline, image, steps, guidance_scale, mc_threshold, seed):     
        image = tensor2pil(image)        
        latent_index = pipeline.generate_dense(image,steps,guidance_scale,mc_threshold,seed)
        
        return (latent_index, pipeline, )      

class Hy3DRefineDenseMeshWithDirect3DS2:
    @classmethod
    def INPUT_TYPES(s):
        return {
            "required": {
                "pipeline": ("HY3DS2PIPELINE",),
                "image": ("IMAGE",),
                "latent_index": ("D3DLATENTINDEX",),
                "sdf_resolution": ([512,1024],{"default":1024}),
                "steps": ("INT",{"default":15}),
                "guidance_scale": ("FLOAT",{"default":7.0,"min":0.0,"max":100.0}),
                "mc_threshold": ("FLOAT",{"default":0.20,"min":0.00,"max":1.00, "step": 0.01}),
                "seed": ("INT",{"default":0,"min":0,"max":0x7fffffff}),
            },
        }

    RETURN_TYPES = ("TRIMESH","HY3DS2PIPELINE", )
    RETURN_NAMES = ("trimesh","pipeline", )
    FUNCTION = "process"
    CATEGORY = "Hy3DS2Wrapper"

    def process(self, pipeline, image, latent_index, sdf_resolution, steps, guidance_scale, mc_threshold, seed):
        image = tensor2pil(image)
        remove_interior = False #no longer required
        if sdf_resolution==1024:
            trimesh = pipeline.refine_dense_1024(image,latent_index,steps,guidance_scale,remove_interior,mc_threshold,seed)
        elif sdf_resolution==512:
            trimesh = pipeline.refine_dense_512(image,latent_index,steps,guidance_scale,remove_interior,mc_threshold,seed)
        else:
            print(f'Unknown sdf_resolution: {sdf_resolution}')
        
        return (trimesh, pipeline,)        
        

NODE_CLASS_MAPPINGS = {
    "Hy3DDirect3DS2ModelLoader": Hy3DDirect3DS2ModelLoader,
    "Hy3DRefineMeshWithDirect3DS2": Hy3DRefineMeshWithDirect3DS2,
    "Hy3DGenerateDenseMeshWithDirect3DS2": Hy3DGenerateDenseMeshWithDirect3DS2,
    "Hy3DRefineDenseMeshWithDirect3DS2": Hy3DRefineDenseMeshWithDirect3DS2,
    }

NODE_DISPLAY_NAME_MAPPINGS = {
    "Hy3DDirect3DS2ModelLoader": "Hy3D Direct3DS2 Model Loader",
    "Hy3DRefineMeshWithDirect3DS2": "Hy3D Refine Mesh With Direct3DS2",
    "Hy3DGenerateDenseMeshWithDirect3DS2": "Hy3D Generate Dense Mesh With Direct3DS2",
    "Hy3DRefineDenseMeshWithDirect3DS2": "Hy3D Refine Dense Mesh With Direct3DS2",
    }

