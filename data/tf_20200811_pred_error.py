import matplotlib.pyplot as plt
import numpy as np
from matplotlib.ticker import (AutoMinorLocator, MultipleLocator)

ax = plt.subplot(111)

# all_data[label id][test error for 100 times of repeated test]
all_data = [
	[13.956024, 14.433242, 36.365207, 10.380671, 13.353506, 50.151516, 11.076908, 12.229296, 13.197254, 44.824227, 35.116051, 64.394852, 10.480341, 13.720852, 88.557334, 37.497621, 11.776002, 11.423172, 55.909996, 16.600878, 14.260358, 13.863314, 48.258129, 15.427159, 9.433073, 11.831115, 10.212262, 12.354979, 43.799844, 12.839599, 56.183875, 11.828298, 12.073554, 85.360527, 49.737677, 11.457030, 9.551553, 12.788189, 14.407476, 36.279821, 37.727431, 53.597884, 10.865854, 49.453092, 11.744117, 16.065677, 49.642372, 60.080919, 10.576669, 56.671448, 11.937124, 59.463291, 12.169872, 9.903194, 68.125121, 9.870735, 12.528896, 12.528278, 13.134355, 92.268916, 10.767118, 10.133862, 12.381869, 11.146730, 12.500672, 11.041856, 46.560807, 11.221893, 15.538326, 11.204281, 53.683452, 11.305852, 13.934167, 44.761666, 11.418703, 39.687794, 39.263057, 14.782713, 56.810139, 57.797041, 13.545492, 63.703707, 13.209352, 12.424941, 14.129963, 56.139439, 14.960313, 58.006742, 52.780320, 10.053853, 11.335702, 13.900809, 13.569368, 8.642230, 12.184775, 44.865501, 49.213126, 11.585204, 13.603950, 47.714876],
	[57.166523, 6.982943, 7.753159, 6.544062, 6.370150, 58.934236, 5.821519, 7.581446, 31.132802, 7.275806, 7.342372, 6.154925, 4.845664, 5.375899, 5.250347, 5.409458, 47.462595, 7.730537, 8.586070, 5.298019, 42.381557, 9.279349, 67.919465, 3.912002, 5.851241, 7.560528, 5.731278, 6.970734, 5.871561, 60.336510, 6.118150, 7.916642, 6.013825, 7.946192, 5.281653, 6.026306, 4.679249, 5.957760, 4.761488, 6.515127, 88.822437, 6.525346, 6.549746, 74.405183, 51.872324, 68.615514, 61.264099, 6.917063, 5.913056, 6.204503, 7.289733, 6.271309, 78.464292, 6.500042, 7.200059, 58.205104, 6.920534, 5.753805, 5.271359, 46.357106, 68.670515, 83.747194, 58.410471, 5.499968, 54.382253, 62.618250, 9.220569, 37.116831, 6.494756, 7.962453, 7.138617, 90.676043, 7.098806, 82.908148, 8.753192, 53.015609, 6.137569, 61.078094, 6.131989, 55.992310, 54.107132, 5.951071, 41.104941, 89.356646, 51.611572, 6.954993, 56.234897, 7.386588, 48.450425, 5.957338, 49.784691, 70.344788, 6.639423, 7.197308, 6.826844, 70.026041, 6.429870, 6.276725, 46.879217, 5.949097],

	[10.515195, 45.434328, 51.414588, 15.402890, 9.034116, 55.602559, 69.823075, 9.650470, 10.743006, 9.906061, 13.349366, 12.244130, 10.966927, 10.924287, 13.885874, 12.222774, 65.770478, 10.316878, 39.384284, 14.644657, 11.249421, 41.947391, 15.741479, 8.898329, 12.156129, 14.583042, 9.001215, 11.812949, 9.541513, 12.339056, 11.040557, 11.452073, 12.472323, 9.783736, 57.167140, 40.489246, 43.608511, 61.649401, 12.363932, 94.788833, 8.643454, 44.381189, 9.933129, 60.882352, 14.526221, 6.390452, 13.461222, 11.654321, 52.283489, 11.649394, 11.015001, 7.754916, 10.947251, 11.494571, 9.674303, 12.610179, 42.955850, 9.670831, 13.405886, 9.058590, 11.744207, 13.501221, 43.761783, 12.304239, 10.588361, 12.464460, 69.354573, 9.949217, 43.991506, 13.569916, 11.333769, 9.802779, 10.881983, 11.404215, 62.910407, 12.273455, 8.903062, 10.182866, 12.851778, 10.193168, 42.471023, 37.978502, 74.606463, 12.347102, 10.987125, 13.633108, 15.169739, 12.006797, 13.912672, 12.773112, 17.140734, 12.890699, 9.391158, 54.936686, 12.315391, 9.843667, 12.330516, 11.492315, 17.803154, 13.282663],
	[6.957377, 64.313606, 56.916500, 45.012399, 5.190539, 6.840786, 57.126490, 53.727270, 7.356256, 6.667304, 6.476066, 5.256859, 6.906330, 70.462762, 7.660466, 7.012304, 7.090306, 6.105001, 6.178831, 66.952585, 61.379096, 7.649414, 7.092564, 5.626575, 5.648277, 64.937245, 7.898103, 5.554414, 7.115182, 6.907596, 9.395951, 5.095690, 43.717707, 6.670211, 39.997334, 5.986490, 3.661167, 69.984995, 4.068519, 5.391135, 5.804065, 66.306257, 34.389040, 5.428332, 6.674271, 58.584086, 5.627205, 36.384092, 88.157494, 6.184002, 5.053334, 6.944832, 6.302980, 7.521219, 7.880967, 53.725881, 44.218278, 6.332340, 7.813679, 6.771026, 6.560170, 7.177000, 4.961530, 9.494012, 66.770047, 4.043301, 4.791953, 6.563170, 5.435724, 5.899271, 5.110108, 7.188095, 5.768912, 65.809342, 7.354027, 6.562866, 4.582166, 5.480870, 6.541070, 36.262630, 6.259378, 6.305899, 51.754131, 6.250982, 6.641401, 35.641656, 9.512910, 4.657524, 7.552413, 61.726023, 8.057250, 53.622812, 7.819604, 5.348360, 5.155510, 75.192289, 59.524181, 69.975642, 5.468333, 5.369140],
	
	[15.341629, 14.678412, 14.665693, 13.633613, 13.982519, 11.178406, 13.697694, 10.461185, 7.996832, 58.611549, 7.431059, 11.076401, 13.597231, 13.139524, 13.495966, 10.273490, 11.452367, 10.167614, 11.316935, 12.724643, 11.802032, 12.646366, 18.280768, 12.672976, 63.965102, 10.045398, 14.369920, 9.424832, 9.887030, 15.149035, 50.458433, 11.725453, 68.129204, 11.349208, 8.817826, 12.674762, 11.424591, 9.955086, 15.008888, 13.119296, 53.527772, 12.351472, 11.709062, 10.334177, 14.370902, 11.150850, 49.790847, 11.347105, 12.541186, 11.364660, 8.964185, 13.050379, 13.894001, 12.638281, 12.705963, 10.696781, 12.950993, 12.468188, 10.024792, 11.244612, 8.306938, 14.698039, 52.269719, 11.344778, 10.537424, 12.616367, 11.841470, 16.306481, 9.616387, 6.608985, 31.705475, 51.275058, 51.768775, 6.900935, 5.397402, 35.518681, 10.463089, 50.515297, 10.941296, 15.312960, 9.595655, 10.641053, 12.715098, 51.306539, 11.187088, 12.041390, 12.267792, 9.327741, 13.728392, 9.170088, 15.267901, 46.450641, 9.717752, 9.947808, 11.213984, 10.757838, 13.362415, 11.037630, 12.577730, 12.897338],
	[7.313773, 7.248264, 5.067444, 8.842977, 7.608721, 8.653894, 62.134383, 5.102347, 5.519440, 81.937181, 5.452010, 8.626065, 8.645136, 7.088186, 5.837918, 54.474982, 5.189909, 6.546465, 4.629266, 70.000192, 9.395889, 4.155231, 5.089076, 4.218533, 74.919280, 6.717917, 53.014032, 3.969286, 8.605599, 4.849044, 60.630324, 65.410434, 6.596810, 62.020646, 71.857941, 5.895819, 68.392247, 65.461754, 3.318386, 5.016175, 8.190272, 57.106412, 4.494780, 7.278212, 6.401423, 7.194343, 6.375702, 4.365551, 64.258294, 7.937419, 6.915316, 7.233481, 4.928482, 6.972867, 5.505200, 6.332903, 6.686514, 8.309472, 4.569678, 4.482371, 6.715820, 57.047777, 7.832438, 6.657865, 4.348972, 70.160085, 41.008560, 3.262324, 7.509626, 4.000181, 5.597471, 6.570225, 9.739884, 5.706723, 7.667952, 6.558006, 65.735783, 9.167421, 55.175922, 7.146803, 5.051127, 7.391683, 3.779323, 97.722961, 51.246519, 3.141432, 4.814731, 5.119214, 7.471215, 69.127114, 56.380045, 6.883611, 6.874813, 4.792079, 3.996554, 7.456756, 7.623711, 34.324562, 6.483892, 6.905238],
	
	[7.588937, 9.964588, 5.913335, 14.336912, 13.438318, 64.998050, 14.631312, 11.699449, 12.018943, 17.035852, 33.823215, 14.634302, 10.590887, 6.548417, 6.542256, 52.161803, 11.575169, 4.272291, 10.748003, 13.584780, 10.553002, 7.793081, 8.634919, 10.588455, 10.426559, 25.592627, 6.528304, 6.643721, 12.551807, 6.212011, 12.035708, 11.079625, 11.048117, 6.524898, 9.812931, 11.548364, 13.711194, 17.094585, 10.433940, 9.024216, 13.412596, 14.670297, 52.645683, 18.038336, 16.261581, 18.772533, 9.714485, 8.775804, 10.928841, 12.796661, 14.090557, 6.190334, 11.486405, 12.780759, 6.899154, 14.491948, 13.350100, 13.927349, 10.942886, 13.221856, 55.177635, 8.480024, 13.393132, 5.715504, 40.622648, 11.076411, 13.566425, 9.729145, 17.015641, 10.556499, 16.986986, 7.519732, 5.639839, 15.001418, 7.281235, 10.256605, 12.889487, 4.466114, 72.670157, 12.998940, 12.355639, 63.626501, 9.878510, 11.465503, 8.883998, 12.721210, 17.134859, 64.329050, 13.715173, 7.411055, 10.270259, 13.054703, 42.645570, 11.476296, 13.285420, 7.498828, 13.339854, 10.922121, 7.470640, 13.386343],
	[7.070935, 5.232053, 4.053272, 62.656732, 5.062426, 9.154989, 7.826296, 75.240219, 59.411407, 4.992232, 76.335673, 10.298458, 5.990009, 6.480765, 56.082957, 7.455960, 75.049727, 5.104337, 10.104645, 8.023306, 5.274468, 5.660575, 41.476173, 4.449734, 4.155806, 54.748757, 79.477174, 4.416540, 5.932159, 6.119858, 6.648595, 7.458493, 6.435249, 4.800734, 14.987605, 10.404192, 6.928261, 6.226757, 55.388482, 8.294353, 5.280141, 11.410110, 63.670893, 53.467722, 5.892295, 11.209773, 5.881369, 3.327046, 3.848187, 7.901931, 8.173404, 2.997336, 2.130338, 10.497846, 10.303912, 5.364018, 47.910314, 44.345466, 4.803757, 3.830554, 8.968367, 3.300144, 51.769722, 6.027482, 5.917627, 12.123815, 9.938891, 7.154330, 56.591873, 77.392856, 52.516691, 8.520153, 7.513137, 8.637736, 5.819892, 1.602784, 66.231463, 4.927559, 7.980261, 4.327471, 7.357223, 6.400759, 2.846838, 4.951876, 11.456527, 9.133238, 7.623498, 5.560661, 2.278189, 32.366328, 5.101368, 7.557821, 51.718406, 8.412207, 7.562392, 4.207905, 85.316843, 2.467279, 9.525035, 3.514291],
	
	[5.802008, 8.326228, 8.165158, 16.077765, 11.404562, 20.139854, 15.238530, 10.588925, 12.392165, 11.837725, 7.154748, 5.875821, 10.407793, 11.690149, 8.048689, 16.255622, 15.526473, 11.459678, 12.052782, 14.200299, 4.264560, 88.325601, 14.532483, 16.565511, 17.438027, 8.135357, 3.824198, 3.467835, 10.596049, 18.631508, 14.997960, 31.864815, 14.841692, 8.964419, 16.878293, 6.764996, 14.238110, 25.446969, 10.016874, 12.221086, 12.377311, 9.950374, 18.485064, 16.869409, 19.702920, 3.454878, 12.833186, 5.692901, 11.024399, 13.564892, 19.688321, 10.989238, 41.383812, 62.427356, 4.397385, 19.694828, 21.198388, 13.547000, 14.133338, 10.734672, 25.351968, 13.517704, 21.242162, 11.340485, 5.907095, 12.182404, 11.363229, 14.015253, 60.685415, 17.550834, 13.884081, 14.959678, 2.485946, 14.537648, 9.575146, 15.711994, 8.527022, 13.957632, 13.892153, 2.227685, 33.477207, 9.593510, 5.948852, 6.847351, 10.919294, 4.699008, 40.898343, 8.204610, 16.438709, 3.779228, 4.759139, 10.056058, 8.071077, 8.019649, 15.380003, 2.677506, 14.841307, 10.080636, 15.473311, 4.767401],
	[43.318922, 6.468859, 9.913257, 55.903135, 7.152742, 6.173400, 2.891720, 4.458493, 27.375968, 10.632708, 5.867643, 64.593774, 4.494313, 7.137831, 1.818833, 5.329690, 8.931935, 8.405413, 1.048821, 65.639816, 4.528360, 8.039385, 6.093105, 11.571390, 62.971126, 5.784095, 51.295974, 25.464415, 6.198689, 74.464807, 57.764465, 1.976959, 2.721981, 6.619558, 2.583216, 5.255425, 2.634106, 36.394218, 6.374182, 74.863759, 75.432708, 59.118688, 1.349001, 51.663599, 94.537953, 4.214529, 41.511808, 6.025560, 2.026080, 4.719750, 2.235685, 54.522440, 3.547660, 6.277774, 6.484477, 69.144590, 91.105996, 26.620359, 6.041705, 8.359846, 4.649439, 11.478467, 33.835395, 49.054471, 62.870999, 0.866062, 8.030661, 1.979249, 18.575960, 5.919261, 7.368157, 5.224024, 52.748799, 10.431567, 5.649330, 6.407153, 18.211424, 5.390051, 4.327642, 6.499954, 6.056569, 0.409378, 13.968299, 13.279833, 28.487376, 74.997135, 63.198839, 38.402265, 0.731686, 56.470007, 1.779763, 3.175988, 4.355366, 13.662878, 10.410555, 62.581137, 2.014510, 79.898078, 3.744049, 1.151870],

	# with threshold B > 4
]

labels = [
	'no threshold\nTrain:Test=6:4', 'B>4\nTrain:Test=6:4', 
	'no threshold\nTrain:Test=7:3', 'B>4\nTrain:Test=7:3', 
	'no threshold\nTrain:Test=8:2', 'B>4\nTrain:Test=8:2',
	'no threshold\nTrain:Test=9:1', 'B>4\nTrain:Test=9:1',
	'no threshold\nTrain:Test=95:5', 'B>4\nTrain:Test=95:5'
	]

bplot = ax.boxplot(all_data[:2*4], patch_artist=True, labels=labels[:2*4])
plt.title('Evaluate the cost model for AMP with Conv2D')

# colors = ['pink', 'lightblue', 'lightgreen']
# for patch, color in zip(bplot['boxes'], colors):
#     patch.set_facecolor(color)

ax.yaxis.set_major_locator(MultipleLocator(10))
ax.yaxis.grid(True, which="both")
# plt.xlabel('Three separate samples')
plt.ylabel('Prediction Error (%)')
plt.show()