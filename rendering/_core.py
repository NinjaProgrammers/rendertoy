import numpy as np
import pyopencl as cl
import pyopencl.array as cla
import pyopencl.tools as cltools
import inspect
import math
import typing


__ctx__ = cl.create_some_context()
__queue__ = cl.CommandQueue(__ctx__)
__code__ = """
#define float4x4 float16

float4x4 transpose( float4x4 m )
{
    float4x4 t;
    // transpose
    t.even = m.lo;
    t.odd = m.hi;
    m.even = t.lo;
    m.odd = t.hi;

    return m;
}

float4 mul(float4 v, float4x4 m) {
    return (float4)(dot(v, m.even.even), dot(v, m.odd.even), dot(v, m.even.odd), dot(v, m.odd.odd));    
}

"""

float2 = cltools.get_or_register_dtype('float2')
float3 = cltools.get_or_register_dtype('float3')
float4 = cltools.get_or_register_dtype('float4')

int2 = cltools.get_or_register_dtype('int2')
int3 = cltools.get_or_register_dtype('int3')
int4 = cltools.get_or_register_dtype('int4')

uint2 = cltools.get_or_register_dtype('uint2')
uint3 = cltools.get_or_register_dtype('uint3')
uint4 = cltools.get_or_register_dtype('uint4')

float4x4 = cltools.get_or_register_dtype('float16')

RGBA = cltools.get_or_register_dtype('uchar4')

image1d_t = 'image1d_t'
image2d_t = 'image2d_t'
image3d_t = 'image3d_t'


def make_float2(*args):
    return np.array(args, dtype=float2)

def make_float3(*args):
    return np.array(args, dtype=float3)

def make_float4(*args):
    return np.array(args, dtype=float4)

def make_float4x4(*args):
    return np.array(args, dtype=float4x4)




def _get_signature(f):
    signature = inspect.signature(f)
    assert all(v.annotation != inspect.Signature.empty for v in signature.parameters.values()), "All arguments needs to be annotated with a type descriptor"
    return [(k, v) for k, v in signature.parameters.items()], signature.return_annotation


__OBJECT_TYPE_TO_CLTYPE__ = {
    cl.mem_object_type.IMAGE1D: 'image1d_t',
    cl.mem_object_type.IMAGE2D: 'image2d_t',
    cl.mem_object_type.IMAGE3D: 'image3d_t',
    cl.mem_object_type.BUFFER: 'buffer_t',
}


def _get_annotation_as_cltype(annotation, assert_no_pointer=False):
    if annotation is None:
        return "void"
    is_pointer = False
    if isinstance(annotation, list):
        assert len(annotation) == 1, "parameters annotated with list should refer to a pointer to a single type, e.g. [int] is considered a int*."
        is_pointer = True
        annotation = annotation[0]
    if isinstance(annotation, str):  # object types
        return "read_write "+annotation
    if assert_no_pointer:
        assert not is_pointer, "Can not use pointers in kernel auxiliary functions"
    return ("__global " if is_pointer else "")+cltools.dtype_to_ctype(annotation) +("*" if is_pointer else "")


def kernel_function(f):
    s, return_annotation = _get_signature(f)
    name = f.__name__
    global __code__

    __code__ += f"""
{_get_annotation_as_cltype(return_annotation, assert_no_pointer=True)} {name}({', '.join(_get_annotation_as_cltype(v.annotation, assert_no_pointer=True) + " " + v.name for k, v in s)}) {{
{inspect.getdoc(f)}
}}
"""
    def wrapper(*args):
        raise Exception("Can not call to this function from host.")
    return wrapper


def kernel_main(f):
    s, return_annotation = _get_signature(f)
    assert return_annotation == inspect.Signature.empty, "Kernel main function must return void"
    name = f.__name__
    global __code__
    __code__ += f"""
__kernel void {name}({', '.join(_get_annotation_as_cltype(v.annotation)+" "+v.name for k,v in s)}) {{
int thread_id = get_global_id(0);
{inspect.getdoc(f)}
}}
    """
    # print(__code__)
    program = None
    class Dispatcher:
        def __init__(self):
            pass
        def __getitem__(self, num_threads):
            if isinstance(num_threads, list) or isinstance(num_threads, tuple):
                num_threads = math.prod(num_threads)
            def resolve_arg(a, annotation):
                if isinstance(a, cla.Array):
                    if isinstance(annotation, list):
                        a = a.data  # case of a pointer, pass the buffer
                    else:
                        a = a.get()  # pass the numpy array as a value transfer.
                if isinstance(a, int):
                    a = np.int32(a)
                return a
            def dispatch_call(*args):
                nonlocal program
                if program is None:
                    program = cl.Program(__ctx__, __code__).build()
                kernel = program.__getattr__(name)
                kernel(__queue__, (num_threads,), None, *[resolve_arg(a, v.annotation) for a, (k,v) in zip(args,s)])
            return dispatch_call
    return Dispatcher()


def kernel_struct(cls):
    fields = cls.__dict__['__annotations__']
    assert all(k in fields.keys() for k in cls.__dict__.keys() if k[0] != "_"), "A public field was declared without annotation"
    dtype = np.dtype([(k, v) for k,v in fields.items()])
    dtype, cltype = cltools.match_dtype_to_c_struct(__ctx__.devices[0], cls.__name__, dtype)
    global __code__
    __code__ += cltype
    cltools.get_or_register_dtype(cls.__name__, dtype)
    return dtype


def create_buffer(count: int, dtype: np.dtype):
    return cla.zeros(__queue__, (count,), dtype)


def create_buffer_from(ary: np.ndarray):
    return cla.to_device(__queue__, ary)


def create_struct(dtype: np.dtype):
    return cla.zeros(__queue__, 1, dtype)[0]


def create_struct_from(ary: np.ndarray):
    return cla.to_device(__queue__, ary.item())


__IMAGE_FORMATS__ = {
    float4: cl.ImageFormat(cl.channel_order.RGBA, cl.channel_type.FLOAT),
    float3: cl.ImageFormat(cl.channel_order.RGB, cl.channel_type.FLOAT),
    float2: cl.ImageFormat(cl.channel_order.RG, cl.channel_type.FLOAT),
    np.float32: cl.ImageFormat(cl.channel_order.R, cl.channel_type.FLOAT),
    RGBA: cl.ImageFormat(cl.channel_order.BGRA, cl.channel_type.UNORM_INT8)
}


__CHANNEL_TYPE_TO_DTYPE__ = {
    cl.channel_type.FLOAT: np.float32,
    cl.channel_type.SIGNED_INT32: np.int32,
    cl.channel_type.SIGNED_INT8: np.int8,
    cl.channel_type.UNSIGNED_INT32: np.uint32,
    cl.channel_type.UNSIGNED_INT8: np.uint8,
    cl.channel_type.UNORM_INT8: np.int8
}


__CHANNEL_ORDER_TO_COMPONENTS__ =  {
    cl.channel_order.BGRA: 4,
    cl.channel_order.RGBA: 4,
    cl.channel_order.RGB: 3,
    cl.channel_order.RG: 2,
    cl.channel_order.R: 1
}


def get_valid_image_formats():
    return __IMAGE_FORMATS__.keys()


Image = cl.Image
Buffer = cl.Buffer


def create_image2d(width: int, height: int, dtype: np.dtype):
    assert dtype in __IMAGE_FORMATS__, "Unsupported dtype for image format"
    return cl.Image(__ctx__, cl.mem_flags.READ_WRITE, __IMAGE_FORMATS__[dtype], shape=(width, height))


def clear(b, value = np.float32(0)):
    if not isinstance(value, np.ndarray):
        value = np.array(value)
    if isinstance(b, Buffer):
        cl.enqueue_fill_buffer(__queue__, b, value, 0, value.nbytes)
    else:
        if math.prod(value.shape) <= 1:
            value = np.array([value]*4)
        cl.enqueue_fill_image(__queue__, b, value, (0,0,0), (b.width, max(1, b.height), max(1, b.depth)))


def mapped(b: typing.Union[cla.Array, Buffer, Image]):
    class _ctx:
        def __init__(self):
            self.mapped = None
        def __enter__(self):
            if isinstance(b, cl.Image):
                dtype = __CHANNEL_TYPE_TO_DTYPE__[b.format.channel_data_type]
                cmps = __CHANNEL_ORDER_TO_COMPONENTS__[b.format.channel_order]
                shape = (b.depth, b.height, b.width, cmps)
                if b.type < cl.mem_object_type.IMAGE3D:
                    shape = shape[1:]
                if b.type < cl.mem_object_type.IMAGE2D:
                    shape = shape[1:]
                if cmps == 1:
                    shape = shape[:-1]
                self.mapped = cl.enqueue_map_image(__queue__, b, cl.map_flags.READ | cl.map_flags.WRITE,
                                                    (0,0,0), (b.width, max(1, b.height), max(1, b.depth)), shape=shape, dtype=dtype)
            elif isinstance(b, cl.Buffer):
                self.mapped = cl.enqueue_map_buffer(__queue__, b, cl.map_flags.READ | cl.map_flags.WRITE,
                                                    b.offset, (b.size,), np.uint8)
            else:
                self.mapped = cl.enqueue_map_buffer(__queue__, b.base_data, cl.map_flags.READ | cl.map_flags.WRITE,
                                                    b.offset, b.shape, b.dtype)
            return self.mapped[0]
        def __exit__(self, exc_type, exc_val, exc_tb):
            self.mapped[0].base.release()
    return _ctx()


def identity():
    return make_float4x4(
        1.0, 0.0, 0.0, 0.0,
        0.0, 1.0, 0.0, 0.0,
        0.0, 0.0, 1.0, 0.0,
        0.0, 0.0, 0.0, 1.0
    )


def translate(*args):
    if len(args) == 1:
        x,y,z = args[0]
    else:
        x,y,z = args
    return make_float4x4(
        1.0, 0.0, 0.0, 0.0,
        0.0, 1.0, 0.0, 0.0,
        0.0, 0.0, 1.0, 0.0,
        x, y, z, 1.0
    )


def scale(*args):
    if len(args) == 1:
        x,y,z = args[0]
    else:
        x,y,z = args
    return make_float4x4(
        x, 0.0, 0.0, 0.0,
        0.0, y, 0.0, 0.0,
        0.0, 0.0, z, 0.0,
        0.0, 0.0, 0.0, 1.0
    )